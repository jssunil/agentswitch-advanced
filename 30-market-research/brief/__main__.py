"""Run the weekly deal brief.

    python3 -m brief --instance {suryodaya,keystone} [--date YYYY-MM-DD] [--top 10]
                     [--since YYYY-MM-DD] [--out briefs/] [--snapshots DIR] [--prompt TEXT]

Reads AGENTSWITCH_BASE_URL and AGENTSWITCH_TOKEN (and AGENTSWITCH_INSTANCE when --instance is
not given, as seat 34's harness runs it). Writes <instance>-<date>.json and .md to --out, by
default the seat's gitignored briefs/ folder, wherever the command is started from.

Standard output: one line per deal shown (rank, deal id, next-step rule), then, as the last line,
`AGENT_RESULT {...}` for seat 34's harness: the claim ("done", or "refused" when the prompt asked
for a change, which this brief never makes) and the shown deal ids under `answer.top_deals`.

Exit codes: 0 done; 2 bad options, or a --since date with no snapshot, or a damaged snapshot;
3 the platform could not be read.
"""
import argparse
import json
import os
import sys

from . import changes, report
from .client import Client, McpError, platform_failure
from .suite import INSTANCES, _iso_date
from .values import display_text

ANSWER_KEY = "top_deals"


def _top(value):
    try:
        n = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected a whole number") from None
    if not 1 <= n <= report.MAX_TOP:
        raise argparse.ArgumentTypeError(f"expected 1 to {report.MAX_TOP}")
    return n


def _date(value):
    try:
        return _iso_date(value)
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from None


def agent_result(brief):
    refused = bool(brief.get("prompt") and brief["prompt"].get("refused"))
    result = {"claim": "refused" if refused else "done",
              "answer": {ANSWER_KEY: [d["row"]["id"] for d in brief["deals"]]}}
    return "AGENT_RESULT " + json.dumps(result, separators=(",", ":"), ensure_ascii=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brief", description="Weekly brief on the largest open deals (read-only)")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"), choices=INSTANCES,
                    required=not os.environ.get("AGENTSWITCH_INSTANCE"))
    ap.add_argument("--date", type=_date, help="the brief's date (default: today in IST)")
    ap.add_argument("--top", type=_top, help=f"deals per company and base currency (default {report.DEFAULT_TOP}, "
                                             "or a number in the prompt)")
    ap.add_argument("--since", type=_date, help="compare with the snapshot of exactly this date")
    ap.add_argument("--out", help="folder for the brief (default: the seat's briefs/ folder)")
    ap.add_argument("--snapshots", help="snapshot folder (default: briefs/snapshots/ for a plain weekly run, "
                                        "briefs/test-snapshots/ with --date or --prompt)")
    # Seat 34's harness appends --prompt. It only picks deals (a number, deal ids); nothing in it
    # is followed as an instruction, and nothing is ever written to the CRM.
    ap.add_argument("--prompt", help="which deals: a number (\"top 5\") and/or deal ids")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    as_of = args.date or report.today_ist()
    if args.since is not None and args.since >= as_of:
        ap.error("--since must be a date before the brief's date")
    folder = changes.snapshot_folder(args.snapshots, date_given=args.date is not None, prompt=args.prompt)
    try:
        brief = report.build(Client(), args.instance, as_of, top=args.top, since=args.since,
                             prompt=args.prompt, snapshots=folder)
    except McpError as e:
        platform_failure("brief", e)
    except changes.NoSnapshot as e:
        print(f"brief: {display_text(e)}", file=sys.stderr)
        return 2
    except changes.SnapshotError as e:
        print(f"brief: stopped, {display_text(e)}", file=sys.stderr)
        return 2

    paths = report.write(brief, report.to_markdown(brief), args.out)
    for note in (brief["prompt"] or {}).get("notes", ()):
        print(f"brief: prompt: {display_text(note)}", file=sys.stderr)
    print(f"brief: wrote {paths[1]} ({len(brief['deals'])} deals, "
          f"{'baseline' if brief['baseline'] else 'compared with ' + str(brief['previous_date'])})", file=sys.stderr)
    for d in brief["deals"]:
        print(f"{d['rank']:>3} {d['row']['id']} {d['next_step']['rule']}")
    sys.stdout.flush()
    print(agent_result(brief), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
