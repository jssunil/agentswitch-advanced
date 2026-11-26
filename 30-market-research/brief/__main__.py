"""Run the weekly deal brief.

    python3 -m brief --instance {suryodaya,keystone} [--date YYYY-MM-DD] [--top 10]
                     [--since YYYY-MM-DD] [--out briefs/] [--snapshots DIR] [--prompt TEXT]
                     [--record DIR | --replay DIR] [--debug]
    python3 -m brief --explain DEAL_ID [--replay DIR] [--date YYYY-MM-DD] [--top 10] [--debug]

Reads AGENTSWITCH_BASE_URL and AGENTSWITCH_TOKEN (and AGENTSWITCH_INSTANCE when --instance is
not given, as seat 34's harness runs it). Writes <instance>-<date>.json and .md to --out, by
default the seat's gitignored briefs/ folder, wherever the command is started from.

--record DIR saves every platform request and reply in DIR (which must be git-ignored, because a
recording holds customer records); --replay DIR runs from such a recording with no platform or token.

--explain DEAL_ID walks the selection rules, account facts and next-step rules (nextstep.explain) for
one deal (brief/explain.py). It prints no deal ID list and no AGENT_RESULT; exit 5 means not found.

Standard output: one line per deal shown (rank, deal id, next-step rule), then, as the last line,
`AGENT_RESULT {...}` for seat 34's harness: the claim ("done", or "refused" when the prompt asked
for a change, which this brief never makes) and the shown deal ids under `answer.top_deals`.

Exit codes: 0 done; 1 an unexpected error; 2 bad options, or a --since date with no snapshot, or a
damaged snapshot; 3 the platform could not be read; 5 deal not found (--explain).

An unexpected error prints its traceback, claims "failed" in `AGENT_RESULT`, and ends with one
plain line, `brief: unexpected error: <type>: <message>`, which seat 34's harness reports as the
reason (agent contract, section 5). --debug also prints, on stderr, each retry, any request over 10 s,
any blocked write, and the traceback of a platform error (brief/debug.py). Seat 34's harness adds
only --prompt, so --debug goes in the command seat 30 registers with it.
"""
import argparse
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from . import changes, debug, explain, report
from .client import RECORDING_FILE, Client, McpError, RecordingClient, ReplayClient, platform_failure
from .suite import (INSTANCES, IST, RECORDING_META, _commit, _iso_date, git_would_track, login_of,
                    read_recording_meta, write_results)
from .values import display_text

ANSWER_KEY = "top_deals"
FAILED_RESULT = 'AGENT_RESULT {"claim":"failed"}'


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


def unexpected(error, prog="brief", claim=True):
    """The traceback, a "failed" claim (stdout), then one plain reason line (stderr, last).
    Seat 34's harness merges stderr into stdout and takes the very last line as the reason on a
    non-zero exit (eval_harness/trial.py), while parse_reported() scans backwards for AGENT_RESULT,
    so the reason line comes last. Every line goes through display_text (bug hunt E2); no token can
    be in it: client errors name settings, never values."""
    for line in traceback.format_exception(type(error), error, error.__traceback__):
        for part in line.rstrip("\n").split("\n"):
            print(display_text(part), file=sys.stderr)
    sys.stderr.flush()
    if claim:
        print(FAILED_RESULT, flush=True)
    print(f"{prog}: unexpected error: {type(error).__name__}: {' '.join(display_text(error).split())}",
          file=sys.stderr, flush=True)


def _apply_replay_meta(ap, args):
    if not (Path(args.replay) / RECORDING_FILE).is_file():
        ap.error(f"--replay: no recording in {args.replay} ({RECORDING_FILE} missing)")
    recorded = read_recording_meta(args.replay)
    rec_inst = recorded.get("instance")
    if rec_inst is not None and rec_inst not in INSTANCES:
        ap.error(f"--replay: unknown instance in {RECORDING_META}: {display_text(rec_inst)}")
    if args.instance and rec_inst and args.instance != rec_inst:
        ap.error(f"--replay: the recording is of {rec_inst}, not {args.instance}")
    args.instance = args.instance or rec_inst
    if args.date is None and recorded.get("date"):
        try:
            args.date = _iso_date(str(recorded["date"]))
        except ValueError:
            ap.error(f"--replay: bad date in {RECORDING_META}: {display_text(recorded['date'])}")


def explain_main(ap, args):
    """--explain: never the deal list or AGENT_RESULT, so it cannot be mistaken for a harness run."""
    if args.prompt is not None or args.out or args.since is not None or args.snapshots or args.record:
        ap.error("--explain can't be combined with --prompt, --out, --since, --snapshots or --record")
    wanted = args.explain.strip()
    if not wanted:
        ap.error("--explain needs a deal id")
    if args.replay:
        _apply_replay_meta(ap, args)
    as_of = args.date or report.today_ist()
    top = args.top or report.DEFAULT_TOP
    try:
        client = ReplayClient(args.replay) if args.replay else Client()
        return explain.run(client, wanted, as_of, top=top)
    except McpError as e:
        debug.crash(e, "brief --explain")
        platform_failure("brief", e)
    except Exception as e:
        unexpected(e, prog="brief --explain", claim=False)
        return 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brief", description="Weekly brief on the largest open deals (read-only)")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"), choices=INSTANCES,
                    help="the system (default: AGENTSWITCH_INSTANCE, or the recording's with --replay)")
    ap.add_argument("--date", type=_date, help="the brief's date (default: today in IST)")
    ap.add_argument("--top", type=_top, help=f"deals per company and base currency (default {report.DEFAULT_TOP}, "
                                             "or a number in the prompt)")
    ap.add_argument("--since", type=_date, help="compare with the snapshot of exactly this date")
    ap.add_argument("--out", help="folder for the brief (default: the seat's briefs/ folder)")
    ap.add_argument("--snapshots", help="snapshot folder (default: briefs/snapshots/ for a plain weekly run, "
                                        "briefs/test-snapshots/ with --date, --prompt or --replay)")
    # Seat 34's harness appends --prompt. It only picks deals (a number, deal ids); nothing in it
    # is followed as an instruction, and nothing is ever written to the CRM.
    ap.add_argument("--prompt", help="which deals: a number (\"top 5\") and/or deal ids")
    ap.add_argument("--debug", action="store_true",
                    help="on stderr: retries, slow requests, blocked writes and tracebacks")
    ap.add_argument("--explain", metavar="DEAL_ID",
                    help="say why one deal is shown or left out, its account facts and next-step rules; "
                         "prints customer data (brief/explain.py)")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--record", metavar="DIR",
                        help="save every platform request and reply in DIR (git-ignored)")
    source.add_argument("--replay", metavar="DIR",
                        help="run from a recording in DIR: no platform, no token")
    args = ap.parse_args(argv)
    debug.ENABLED = args.debug
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    if args.explain is not None:
        return explain_main(ap, args)

    date_given = args.date is not None
    if args.replay:
        _apply_replay_meta(ap, args)
    if not args.instance:
        ap.error("--instance is required (or set AGENTSWITCH_INSTANCE)")
    if args.record:
        if git_would_track(args.record):
            ap.error(f"--record: {args.record} is a folder git would track, and a recording holds customer "
                     "records. Use one git ignores, e.g. briefs/recordings/<instance>-<date>")
        if (Path(args.record) / RECORDING_FILE).exists():
            ap.error(f"--record: {args.record} already holds a recording ({RECORDING_FILE} exists)")

    as_of = args.date or report.today_ist()
    if args.since is not None and args.since >= as_of:
        ap.error("--since must be a date before the brief's date")
    folder = changes.snapshot_folder(args.snapshots, date_given=date_given or bool(args.replay),
                                     prompt=args.prompt)
    started = datetime.now(IST)
    try:
        if args.replay:
            client = ReplayClient(args.replay)
        elif args.record:
            client = RecordingClient(args.record)
            client.tool_names()
            write_results(Path(args.record) / RECORDING_META,
                          {"instance": args.instance, "date": as_of.isoformat(),
                           "recorded_at": started.isoformat(timespec="seconds"),
                           "commit": _commit(), "login": login_of(client)})
        else:
            client = Client()
        brief = report.build(client, args.instance, as_of, top=args.top, since=args.since,
                             prompt=args.prompt, snapshots=folder)
        paths = report.write(brief, report.to_markdown(brief), args.out)
    except McpError as e:
        debug.crash(e, "brief")
        platform_failure("brief", e)
    except changes.NoSnapshot as e:
        print(f"brief: {display_text(e)}", file=sys.stderr)
        return 2
    except changes.SnapshotError as e:
        print(f"brief: stopped, {display_text(e)}", file=sys.stderr)
        return 2
    except Exception as e:                           # anything else: say what failed, claim "failed"
        unexpected(e)
        return 1

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
