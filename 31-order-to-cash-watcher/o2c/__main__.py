"""Run the daily exception report.

    python3 -m o2c --days 7 --instance suryodaya --out reports/

Reads AGENTSWITCH_BASE_URL and AGENTSWITCH_TOKEN from the environment.

Standard output carries only the record ids of the orders not invoiced, one per line, oldest
first, so seat 34 can read it without filtering. Everything else (the summary, warnings) goes to
standard error. `--markdown` prints the full report on standard output instead, for a person.
Exit 4 means the report was produced but the ID list may be missing orders; stderr says why.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import prompt as prompt_mod, report
from .client import Client, McpError, platform_failure
from .output import EXIT_INCOMPLETE, days_option, instance_label, order_ids, order_list_gaps, report_stem, safe_console, write_pair

nonnegative_days = days_option   # older name, kept for callers


def write_report(rep, md, out, instance):
    """Write <instance>-<date>.json and .md as UTF-8, whatever the machine's default encoding.

    Returns the file stem. Customer names can be any script (e.g. 客户); a cp1252 default on
    Windows would otherwise fail on the Markdown after the JSON was already written.
    """
    stem = report_stem(instance, rep["report_date"])
    # Serialize before touching output files. NaN/Infinity must never appear in JSON.
    encoded = json.dumps(rep, indent=2, allow_nan=False)
    write_pair(out, stem, encoded, md)
    return stem


def main():
    ap = argparse.ArgumentParser(prog="o2c", description="Daily order-to-cash exception report (read-only)")
    ap.add_argument("--days", type=days_option,
                    help="N: nonnegative days before an order counts as not invoiced (default: the prompt, then O2C_DAYS, then 7)")
    ap.add_argument("--date", type=date.fromisoformat, help="report date (default: today in IST)")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"), help="label for the report, e.g. suryodaya")
    ap.add_argument("--out", type=Path, help="folder to write <instance>-<date>.md and .json")
    ap.add_argument("--markdown", action="store_true",
                    help="print the full Markdown report on stdout instead of the order ID list")
    # Seat 34 appends this argument to every seat command. Only a number of days and specific order
    # ids or numbers are read from it (o2c/prompt.py); instructions in it are never followed.
    ap.add_argument("--prompt", help="the \"which orders\" question: a number of days and/or order ids; no model is called")
    args = ap.parse_args()
    safe_console()
    try:
        instance_label(args.instance)
    except ValueError as e:
        ap.error(str(e))
    asked = prompt_mod.read(args.prompt)
    notes = list(asked.notes)
    if args.days is not None:
        days = args.days
        if asked.days is not None and asked.days != args.days:
            notes = [n for n in notes if not n.startswith("orders more than")] + [f"--days {args.days} wins over the prompt"]
    elif asked.days is not None:
        days = asked.days
    else:
        try:
            days = days_option(os.environ.get("O2C_DAYS", "7"))
        except argparse.ArgumentTypeError as e:
            ap.error(f"O2C_DAYS: {e}")
    args.days = days
    if args.prompt is not None and notes:
        print(f"o2c: prompt: {'; '.join(notes)}", file=sys.stderr)

    try:
        rep = report.build(Client(), report_date=args.date, days=args.days, instance=args.instance)
    except McpError as e:
        platform_failure("o2c", e)
    md = report.to_markdown(rep)
    if args.out:
        stem = write_report(rep, md, args.out, args.instance)
        counts = ", ".join(f"{k}={len(v['rows'])}" if v["status"] == "ok" else f"{k}={v['status']}" for k, v in rep["sections"].items())
        issues = rep["sections"]["orders_not_invoiced"].get("date_issues", [])
        if issues:
            counts += f", order_date_issues={len(issues)}"
        validation = sum(len(sec.get("data_issues", [])) for sec in rep["sections"].values())
        if validation:
            counts += f", validation_issues={validation} (partial coverage)"
        print(f"wrote {args.out / stem}.md ({counts})", file=sys.stderr)
    ids = order_ids(rep, asked.orders)
    gaps = order_list_gaps(rep, asked.orders)
    if args.markdown:
        print(md)
    else:
        if asked.orders:
            print(f"o2c: {len(ids)} of the {len(asked.orders)} order(s) named are not invoiced after {args.days} days "
                  "(the others are invoiced, too recent or not found)", file=sys.stderr)
        elif not args.out:
            print(f"o2c: {len(ids)} orders not invoiced after {args.days} days on {rep['report_date']}; "
                  "order record ids on stdout, oldest first (--out or --markdown for the full report)", file=sys.stderr)
        sys.stdout.write("".join(i + "\n" for i in ids))
    if gaps:
        sys.stdout.flush()   # the ids come before the warning when the harness merges the two streams
        print(f"o2c: the order list may be incomplete: {'; '.join(gaps)}", file=sys.stderr)
        raise SystemExit(EXIT_INCOMPLETE)


if __name__ == "__main__":
    main()
