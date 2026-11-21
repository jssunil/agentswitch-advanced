"""Unified v1 suite runner: writes results.json in 34-eval-harness/TASK_FORMAT.md across all seat 31 tasks.

Combines the order tasks (tasks_orders.TASKS) and payment/draft/read-only tasks (tasks_payments.TASKS)
dynamically so new tasks are never dropped by a fixed count.

    python3 -m o2c.suite --instance suryodaya --date 2026-11-21 --agent --out results.json
"""
import argparse
import json
import os
from datetime import date
from pathlib import Path

from . import report, tasks_orders, tasks_payments
from .client import Client, McpError, platform_failure
from .output import days_option, safe_console
from .tasks_payments import Ctx, command_agent, run
from .values import display_text


def all_tasks():
    """Every seat 31 suite task from tasks_orders and tasks_payments, in run order."""
    return list(tasks_orders.TASKS) + list(tasks_payments.TASKS)


def run_suite(ctx, tasks=None):
    """Run the combined suite against `ctx` and return the TASK_FORMAT.md payload."""
    selected = all_tasks() if tasks is None else list(tasks)
    results = run(ctx, selected)
    passed = sum(1 for r in results if r["passed"])
    return {
        "tasks": results,
        "summary": f"{ctx.instance}: {passed}/{len(results)} tasks passed",
    }


def write_results(path, payload):
    """Write `results.json` as UTF-8, refusing NaN/Infinity."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, allow_nan=False)
    target.write_text(encoded, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(prog="o2c.suite", description="Run the full seat 31 suite and write results.json")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"),
                    required=not os.environ.get("AGENTSWITCH_INSTANCE"), help="instance label (suryodaya or keystone)")
    ap.add_argument("--date", type=date.fromisoformat, help="report date (default: today in IST)")
    ap.add_argument("--days", type=days_option, default=os.environ.get("O2C_DAYS", "7"))
    ap.add_argument("--mailbox-id", default=os.environ.get("O2C_MAILBOX_ID"))
    ap.add_argument("--accounts-email", default=os.environ.get("O2C_ACCOUNTS_EMAIL"))
    ap.add_argument("--allow-writes", action="store_true", help="let task 9 create a draft (harness copy only)")
    ap.add_argument("--agent", action="store_true",
                    help="tasks 10 and 11: run the real agent (python3 -m o2c --prompt) instead of guardrails only")
    ap.add_argument("--out", help="write results JSON here (e.g. results.json)")
    args = ap.parse_args()
    safe_console()

    try:
        client = Client()
    except McpError as e:
        platform_failure("o2c.suite", e)

    report_date = args.date or report.today_ist()
    ctx = Ctx(
        client,
        args.instance,
        report_date,
        args.days,
        mailbox_id=args.mailbox_id,
        accounts_email=args.accounts_email,
        agent=command_agent(ap, args.agent),
        allow_writes=args.allow_writes or bool(os.environ.get("AGENTSWITCH_RUN_ID")),
    )
    out = run_suite(ctx)
    if args.out:
        write_results(args.out, out)
    for r in out["tasks"]:
        print(display_text(f"{'PASS' if r['passed'] else 'FAIL'} {r['score']:.1f} {r['id']}: {r['evidence']}"))
    print(out["summary"])


if __name__ == "__main__":
    main()
