"""Section A: orders not invoiced after N days.

Rule (default, to be confirmed with the seat lead): a submitted sales order
(docstatus 1) that is confirmed or delivered, still unbilled, whose age in days since
the order date is greater than N. Draft and cancelled orders are ignored.
"""
from datetime import date

ELIGIBLE_STATUS = {"confirmed", "delivered"}


def age_days(order, today):
    return (today - date.fromisoformat(str(order["date"])[:10])).days


def unbilled_orders(orders, today, days):
    """Return exception rows (oldest first) for orders older than `days` and still unbilled."""
    rows = []
    for o in orders:
        if o.get("docstatus") != 1 or o.get("invoiced_status") != "unbilled":
            continue
        if o.get("status") not in ELIGIBLE_STATUS or not o.get("date"):
            continue
        age = age_days(o, today)
        if age <= days:
            continue
        rows.append({
            "id": o["id"], "number": o.get("number"), "date": str(o["date"])[:10], "age_days": age,
            "status": o["status"], "delivered_status": o.get("delivered_status"),
            "customer": o.get("_party_id_display") or o.get("party_id"),
            "grand_total": o.get("grand_total"), "currency": o.get("currency_code"),
        })
    return sorted(rows, key=lambda r: (-r["age_days"], str(r["number"])))


def fetch_unbilled_orders(client):
    # Narrow server-side to what could qualify; unbilled_orders() re-checks every rule.
    return client.list_all("SalesOrder.list", invoiced_status="unbilled", docstatus=1)
