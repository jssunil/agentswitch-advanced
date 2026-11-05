"""Daily exception report CLI (section A for now). Read-only.

    python -m otc_watcher.report --system suryodaya --days 7 [--env-file PATH] [--json OUT]
"""
import argparse
import json
from datetime import date

from .mcp import MCPClient, load_env_file
from .orders import fetch_unbilled_orders, unbilled_orders


def render(system, today, days, rows):
    lines = [f"# Order-to-Cash exception report - {system} - {today.isoformat()}", "",
             f"## A. Orders not invoiced after {days} days ({len(rows)})", ""]
    if not rows:
        lines.append("_None._")
    else:
        lines += ["| Order | Date | Age (days) | Status | Delivered | Customer | Total |",
                  "|---|---|---:|---|---|---|---:|"]
        lines += [f"| {r['number']} | {r['date']} | {r['age_days']} | {r['status']} | {r['delivered_status']} | "
                  f"{r['customer']} | {r['grand_total']} {r['currency'] or ''} |" for r in rows]
    lines += ["", "## B. Invoices overdue", "", "_Not implemented yet._",
              "", "## C. Payments not applied", "", "_Not implemented yet._", ""]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--system", choices=["suryodaya", "keystone"], required=True)
    ap.add_argument("--days", type=int, default=7, help="N: minimum age in days (default 7)")
    ap.add_argument("--env-file", help="KEY=value credentials file kept outside the repo")
    ap.add_argument("--json", help="also write rows to this JSON file")
    a = ap.parse_args(argv)
    if a.env_file:
        load_env_file(a.env_file)
    today = date.today()
    client = MCPClient(a.system)
    rows = unbilled_orders(fetch_unbilled_orders(client), today, a.days)
    print(render(a.system, today, a.days, rows))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=1)


if __name__ == "__main__":
    main()
