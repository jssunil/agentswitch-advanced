"""Seat 30 suite runner: runs the 12 tasks and writes results.json (34-eval-harness/TASK_FORMAT.md).

    python3 -m brief.suite --instance keystone --out results.json [--date YYYY-MM-DD] [--only id,id] [--debug]

Tasks 1-6 come from tasks_deals.TASKS (Sunil) and 7-12 from tasks_brief.TASKS (Deepak), in suite
order. Every planned task is reported. A task whose module isn't built yet, or that crashes, is
a failure marked infrastructure, never dropped. If the platform can't be reached at all,
results.json is still written (every task an infrastructure failure), and the exit code is 3.

--date defaults to today in IST, so a harness run needs no date. Snapshots and other scratch
files go to a temporary folder that is removed afterwards.

--debug prints, on stderr, a line as each task starts and ends (with its time) and the full
traceback of any crash (brief/debug.py). The evidence lines and results.json don't change.
"""
import argparse
import json
import os
import re
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import debug, optional_module
from .client import EXIT_PLATFORM, Client, McpError
from .values import display_text

# Suite numbers 1-12 (PLAN.md, "v1 suite (30 Nov): tasks").
PLANNED = ("top10_ids", "open_only", "unknown_stage", "currency_safe", "zero_value", "tie_break",
           "account_facts", "what_changed", "no_invented_change", "next_step_rule", "refuse_update",
           "brief_read_only")
IST = timezone(timedelta(hours=5, minutes=30))
INSTANCES = ("suryodaya", "keystone")


def today_ist():
    return datetime.now(IST).date()


def all_tasks():
    """[(task_id, title, fn or None, why_missing)] for every planned task, in suite order, then any
    extra task a module adds (so a new task is never dropped)."""
    found, problems = {}, []
    for module in ("tasks_deals", "tasks_brief"):
        try:
            mod = optional_module(module)
        except Exception as e:      # the file exists but can't be imported: say why
            debug.crash(e, f"import of brief.{module}")
            problems.append(f"{module} could not be imported: {type(e).__name__}: {e}")
            continue
        if mod is None:
            problems.append(f"{module} not built yet")
            continue
        for task_id, title, fn in getattr(mod, "TASKS", ()):
            found.setdefault(task_id, (title, fn))
    why = "; ".join(problems) or "task not in any TASKS list"
    tasks = [(t, *found[t], None) if t in found else (t, t.replace("_", " "), None, why) for t in PLANNED]
    tasks += [(t, title, fn, None) for t, (title, fn) in found.items() if t not in PLANNED]
    return tasks


def run_suite(client, instance, as_of, only=None, platform_error=None):
    """The TASK_FORMAT.md payload. With `platform_error`, nothing runs and every task says why."""
    results = []
    with tempfile.TemporaryDirectory(prefix="seat30-suite-") as workdir:
        ctx = {"instance": instance, "as_of": as_of, "workdir": workdir}
        for task_id, title, fn, missing in all_tasks():
            if only and task_id not in only:
                continue
            if platform_error is not None:
                r = _infrastructure(f"the platform could not be reached: {platform_error}")
            elif fn is None:
                r = _infrastructure(missing)
            else:
                debug.step(f"{task_id}: started")
                started = time.monotonic()
                debug.current = task_id
                try:
                    r = fn(client, ctx)
                except Exception as e:      # tasks catch their own errors; this is the last guard
                    debug.crash(e, task_id)
                    r = _infrastructure(f"{type(e).__name__}: {e}")
                finally:
                    debug.current = ""
                r = _checked(r)
                debug.step(f"{task_id}: {'pass' if r['passed'] else 'FAIL'} {r['score']:.2f} "
                           f"in {time.monotonic() - started:.1f} s")
            results.append({"id": f"{instance}:{task_id}", "title": title, **r})
    passed = sum(1 for r in results if r["passed"])
    return {"tasks": results, "summary": f"{instance}: {passed}/{len(results)} tasks passed"}


def write_results(path, payload):
    """UTF-8 JSON, written in full or not at all; NaN and Infinity refused."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, allow_nan=False, ensure_ascii=False)
    fd, temporary = tempfile.mkstemp(prefix=".results-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def _infrastructure(reason):
    return {"passed": False, "score": 0.0, "evidence": f"infrastructure: {_one_line(reason)[:200]}"}


def _one_line(text):
    """Evidence is printed one task per line: no control characters and no line breaks of any kind,
    so server text inside an error can't add a line that looks like a result (bug hunt BH-11)."""
    return " ".join(display_text(text).split())


def _iso_date(value):
    """--date: YYYY-MM-DD only. date.fromisoformat also takes 20261124 and 2026-W48-2 (bug hunt BH-12)."""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(value)
    return date.fromisoformat(value)


def _checked(r):
    """A task result in the required shape, or an infrastructure failure saying what was wrong."""
    if not isinstance(r, dict) or not isinstance(r.get("passed"), bool) or not isinstance(r.get("evidence"), str):
        return _infrastructure("the task returned a result in the wrong shape")
    score = r.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.0 <= score <= 1.0:
        return _infrastructure("the task returned a score outside 0.0-1.0")
    if r["passed"] and score == 0.0:
        return _infrastructure("the task passed with a score of 0")
    if not r["passed"] and score == 1.0:      # bug hunt BH-10: a failure never gets full marks
        return _infrastructure("the task failed with a full score")
    return {"passed": r["passed"], "score": float(score), "evidence": _one_line(r["evidence"])}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="brief.suite", description="Run the seat 30 suite and write results.json")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"), choices=INSTANCES,
                    required=not os.environ.get("AGENTSWITCH_INSTANCE"))
    ap.add_argument("--date", type=_iso_date, help="the brief's date (default: today in IST)")
    ap.add_argument("--only", type=lambda v: {x.strip() for x in v.split(",") if x.strip()},
                    help="comma-separated task ids")
    ap.add_argument("--out", help="write results JSON here (e.g. results.json)")
    ap.add_argument("--debug", action="store_true",
                    help="on stderr: each task's start, end and time, and the full traceback of any crash")
    args = ap.parse_args(argv)
    debug.ENABLED = args.debug
    if args.only and args.only - set(PLANNED):
        ap.error(f"unknown task ids: {', '.join(sorted(args.only - set(PLANNED)))}")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    client, platform_error = None, None
    try:
        client = Client()
        # Bug hunt BH-02 (24 Nov): Client() only checks the settings. One read first, so a platform
        # that is down or a rejected token gives exit 3, not twelve task failures and exit 0.
        client.tool_names()
    except McpError as e:
        debug.crash(e, "the first platform read")
        client, platform_error = None, display_text(e)
    out = run_suite(client, args.instance, args.date or today_ist(), args.only, platform_error)
    if args.out:
        write_results(args.out, out)
    for r in out["tasks"]:
        print(f"{'PASS' if r['passed'] else 'FAIL'} {r['score']:.2f} {r['id']}: {r['evidence']}")
    print(out["summary"])
    return EXIT_PLATFORM if platform_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
