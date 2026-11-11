"""Daily order-to-cash exception report.

The rules are pure functions over rows so they can be tested without the platform.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

IST = timezone(timedelta(hours=5, minutes=30))
SUBMITTED = 1
CANCELLED = 2
NOT_INVOICED = ("unbilled", "partially_invoiced")
RECEIVABLE = "receivable"
FIXTURE_MARKERS = ("test", "harness", "fixture")
AGE_BUCKETS = ("0-7", "8-14", "15-30", "31-60", ">60")


def today_ist():
    return datetime.now(IST).date()


def looks_like_fixture(row):
    text = " ".join(str(row.get(k) or "") for k in ("_party_id_display", "notes", "_deal_id_display")).lower()
    return any(m in text for m in FIXTURE_MARKERS)


UNKNOWN_CURRENCY = "currency not recorded"


def _code(value):
    code = str(value or "").strip().upper()
    return code or None


def currency_context(client, names=None):
    """Where currency is recorded on the platform (probed 11 Nov 2026).

    Sales orders have a `currency_code` field but it is empty on every submitted order, and
    payments have no currency field at all. The customer (`Party.currency_id`) and the company
    (`Company.default_currency`) do record one. Returns {"parties": {id: code}, "companies":
    {id: code}}; a source this login cannot read is left empty.
    """
    names = client.tool_names() if names is None else names
    parties = {p.get("id"): _code(p.get("currency_id")) for p in client.list_all("Party")} if "Party.list" in names else {}
    companies = {c.get("id"): _code(c.get("default_currency")) for c in client.list_all("Company")} if "Company.list" in names else {}
    return {"parties": parties, "companies": companies}


def resolve_currency(record_code, party_id, company_id, ctx):
    """(code, source): the record's own currency, else the customer's, else the company's default.

    Never guessed from the instance name or an exchange rate. With one company on the system its
    default applies when the row has no company_id. Returns (None, None) if nothing is recorded.
    """
    ctx = ctx or {}
    companies = ctx.get("companies") or {}
    company = companies.get(company_id) if company_id in companies else (
        next(iter(companies.values())) if len(companies) == 1 else None)
    for source, value in (("record", record_code), ("customer", (ctx.get("parties") or {}).get(party_id)),
                          ("company", company)):
        code = _code(value)
        if code:
            return code, source
    return None, None


def apply_currency(rows, ctx):
    """Set `currency` and `currency_source` on report rows (which carry party_id/company_id)."""
    for r in rows:
        r["currency"], r["currency_source"] = resolve_currency(r.get("currency"), r.get("party_id"), r.get("company_id"), ctx)
    return rows


def _as_date(value):
    return date.fromisoformat(str(value)[:10]) if value else None


def validate_days(days):
    """Reject invalid thresholds before a client or a report run is started."""
    if isinstance(days, bool) or not isinstance(days, int) or days < 0:
        raise ValueError("days must be a nonnegative integer")
    return days


def age_bucket(age_days):
    """Order-date age bucket; negative/unknown ages are not bucketed."""
    if age_days is None or age_days < 0:
        return None
    for maximum, label in zip((7, 14, 30, 60), AGE_BUCKETS):
        if age_days <= maximum:
            return label
    return AGE_BUCKETS[-1]


def _order_date(row):
    try:
        return _as_date(row.get("date"))
    except (ValueError, TypeError):
        return None


def order_date_issues(orders, report_date):
    """Unbilled submitted orders whose order dates cannot support an age check.

    These are diagnostics, not additional exception rows or financial totals.
    """
    issues = []
    for row in orders:
        if row.get("docstatus") != SUBMITTED or row.get("invoiced_status") not in NOT_INVOICED:
            continue
        day = _order_date(row)
        reason = None
        if day is None:
            reason = "missing order date" if not row.get("date") else "invalid order date"
        elif day > report_date:
            reason = "future order date"
        if reason:
            issues.append({"id": row.get("id"), "number": row.get("number"), "reason": reason})
    return sorted(issues, key=lambda r: (str(r["number"] or ""), str(r["id"] or "")))


def _order_sort_key(row):
    """Oldest first, then currency group, then descending value within the group."""
    currency = _code(row.get("currency"))
    try:
        amount = Decimal(str(row.get("grand_total")))
        if not amount.is_finite():
            amount = None
    except InvalidOperation:
        amount = None
    return (-row["age_days"], currency is None, currency or "", amount is None,
            amount.copy_negate() if amount is not None else Decimal(0), str(row.get("number") or ""), str(row.get("id") or ""))


def sort_orders(rows):
    """Sort again after currency resolution so fallback codes determine the groups too."""
    return sorted(rows, key=_order_sort_key)


def order_age_summary(rows):
    counts = dict.fromkeys(AGE_BUCKETS, 0)
    for row in rows:
        bucket = age_bucket(row.get("age_days"))
        if bucket is not None:
            counts[bucket] += 1
    return counts


def orders_not_invoiced(orders, report_date, days):
    """Submitted orders not fully invoiced, strictly older than N by order date.

    No verified deliverable-date field is agreed yet; delivery state is not used
    to invent one. Invalid/missing/future dates are surfaced by order_date_issues.
    """
    validate_days(days)
    out = []
    for o in orders:
        if o.get("docstatus") != SUBMITTED or o.get("invoiced_status") not in NOT_INVOICED:
            continue
        order_date = _order_date(o)
        if order_date is None:
            continue
        age = (report_date - order_date).days
        if age <= days:
            continue
        out.append({
            "number": o.get("number"),
            "id": o.get("id"),
            "customer": o.get("_party_id_display"),
            "party_id": o.get("party_id"),
            "company_id": o.get("company_id"),
            "order_date": order_date.isoformat(),
            "age_days": age,
            "age_basis": "order_date",
            "age_bucket": age_bucket(age),
            "deliverability_status": "unverified",
            "invoiced_status": o.get("invoiced_status"),
            "delivered_status": o.get("delivered_status"),
            "grand_total": o.get("grand_total"),
            "currency": o.get("currency_code"),
            "fixture": looks_like_fixture(o),
        })
    return sort_orders(out)


def payments_not_applied(payments, report_date):
    """Incoming payments (draft or submitted, not cancelled) with money not yet allocated to an invoice.

    Drafts are kept: the instructor counts all 40 Suryodaya receive payments as unallocated.
    The status column shows which ones are drafts.
    """
    out = []
    for p in payments:
        if p.get("type") != "receive" or p.get("docstatus") == CANCELLED:
            continue
        unallocated = round(float(p.get("unallocated") or 0), 2)
        if unallocated <= 0:
            continue
        paid = _as_date(p.get("date"))
        out.append({
            "number": p.get("number"),
            "id": p.get("id"),
            "customer": p.get("_party_id_display"),
            "party_id": p.get("party_id"),
            "company_id": p.get("company_id"),
            "currency": None,   # Payment has no currency field; resolved from customer/company
            "payment_date": paid.isoformat() if paid else None,
            "age_days": (report_date - paid).days if paid else None,
            "amount": p.get("amount"),
            "unallocated": unallocated,
            "status": p.get("status"),
            "fixture": looks_like_fixture(p),
        })
    return sorted(out, key=lambda r: (-(r["age_days"] or 0), r["number"] or ""))


def invoices_overdue(invoices, report_date):
    """Submitted invoices past their due date with a balance left, plus invoices with no due date.

    Due date and balance are read from the Invoice entity (`due_date`, `outstanding`), not
    derived (instructor, 7 Nov). Any submitted invoice with an empty `due_date` is reported
    separately, whatever its balance. Field names checked against Invoice.list on 11 Nov.

    Only customer invoices count: the Invoice entity also holds supplier bills (`direction` =
    "payable"; 165 of 487 on Suryodaya, 11 Nov), which must never appear here or get a reminder.
    Returns (overdue, no_due_date).
    """
    overdue, no_due = [], []
    for inv in invoices:
        if inv.get("docstatus") != SUBMITTED or inv.get("direction", RECEIVABLE) != RECEIVABLE:
            continue
        outstanding = round(float(inv.get("outstanding") or 0), 2)
        due = _as_date(inv.get("due_date"))
        row = {
            "number": inv.get("number"),
            "id": inv.get("id"),
            "customer": inv.get("_party_id_display"),
            "party_id": inv.get("party_id"),
            "company_id": inv.get("company_id"),
            "due_date": due.isoformat() if due else None,
            "days_overdue": (report_date - due).days if due else None,
            "payment_terms": inv.get("payment_terms"),
            "grand_total": inv.get("grand_total"),
            "amount_paid": inv.get("amount_paid"),
            "outstanding": outstanding,
            "currency": inv.get("currency_code"),
            "fixture": looks_like_fixture(inv),
        }
        if due is None:
            no_due.append(row)
        elif due < report_date and outstanding > 0:
            overdue.append(row)
    overdue.sort(key=lambda r: (-r["days_overdue"], r["number"] or ""))
    no_due.sort(key=lambda r: r["number"] or "")
    return overdue, no_due


def build(client, report_date=None, days=7, instance=None, currencies=None):
    validate_days(days)
    report_date = report_date or today_ist()
    sections = {}
    names = client.tool_names()
    currencies = currencies if currencies is not None else currency_context(client, names)

    orders = client.list_all("SalesOrder", {"docstatus": SUBMITTED})
    sections["orders_not_invoiced"] = {
        "status": "ok",
        "rule": f"submitted, invoiced_status in {list(NOT_INVOICED)}, order date more than {days} days ago",
        "rows": sort_orders(apply_currency(orders_not_invoiced(orders, report_date, days), currencies)),
    }
    order_section = sections["orders_not_invoiced"]
    order_section.update(
        age_basis="order_date",
        deliverability_status="unverified",
        unverified_deliverability_count=len(order_section["rows"]),
        age_buckets=order_age_summary(order_section["rows"]),
        date_issues=order_date_issues(orders, report_date),
    )

    if "Invoice.list" in names:
        overdue, no_due = invoices_overdue(client.list_all("Invoice", {"docstatus": SUBMITTED}), report_date)
        apply_currency(overdue, currencies)
        apply_currency(no_due, currencies)
        sections["invoices_overdue"] = {
            "status": "ok",
            "rule": "customer invoices (receivable), submitted, due_date before the report date, outstanding > 0",
            "rows": overdue,
            "no_due_date": no_due,
        }
    else:
        sections["invoices_overdue"] = {
            "status": "blocked",
            "reason": "this login has no invoice read tool (no Invoice.list; invoice nodes in "
                      "endpoint.make.thread are redacted). Tracked as BUG-31-001",
            "rows": [],
        }

    payments = client.list_all("Payment", {"type": "receive"})
    sections["payments_not_applied"] = {
        "status": "ok",
        "rule": "incoming (receive), not cancelled, unallocated > 0",
        "rows": apply_currency(payments_not_applied(payments, report_date), currencies),
    }

    return {
        "instance": instance,
        "report_date": report_date.isoformat(),
        "days": days,
        "generated_at": datetime.now(IST).isoformat(timespec="seconds"),
        "sections": sections,
    }


TITLES = {
    "orders_not_invoiced": "Orders not invoiced",
    "invoices_overdue": "Invoices overdue",
    "payments_not_applied": "Payments not applied",
}


def _money(v):
    return f"{v:,.2f}" if isinstance(v, (int, float)) else ""


def _cur(r):
    """Currency label for a table cell: the code, or an explicit 'currency not recorded'."""
    return r.get("currency") or UNKNOWN_CURRENCY


def currency_sources(report):
    """Count rows by where their currency came from, across every section."""
    counts = {}
    for sec in report["sections"].values():
        for r in sec.get("rows", []) + sec.get("no_due_date", []):
            key = r.get("currency_source") or "not recorded"
            counts[key] = counts.get(key, 0) + 1
    return counts


def cell(v):
    """Text from a platform record, safe inside a Markdown table cell.

    Escapes backslashes and pipes (so a name like `Acme | North` stays one column) and turns
    CR/LF into spaces (so a line break cannot split the row). None becomes an empty cell.
    """
    if v is None:
        return ""
    text = str(v).replace("\\", "\\\\").replace("|", "\\|")
    return " ".join(text.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()


def _invoice_rows(rows):
    out = ["| Invoice | Customer | Due | Days overdue | Terms | Total | Paid | Outstanding | |",
           "|---|---|---|---:|---|---:|---:|---:|---|"]
    for r in rows:
        out.append(f"| {cell(r['number'])} | {cell(r['customer'])} | {cell(r['due_date'])} | "
                   f"{r['days_overdue'] if r['days_overdue'] is not None else ''} | {cell(r['payment_terms'])} | "
                   f"{_money(r['grand_total'])} | {_money(r['amount_paid'])} | {_money(r['outstanding'])} {cell(_cur(r))} | "
                   f"{'fixture' if r['fixture'] else ''} |")
    return out


def _invoice_tables(rows, no_due):
    lines = (_invoice_rows(rows) if rows else ["None."]) + [""]
    lines += [f"### Invoices with no due date ({len(no_due)})", ""]
    lines += (_invoice_rows(no_due) if no_due else ["None."]) + [""]
    return lines


def to_markdown(report):
    lines = [
        f"# Order-to-cash exceptions — {report.get('instance') or ''} {report['report_date']}",
        "",
        f"N = {report['days']} days. Generated {report['generated_at']} (IST). Read-only; nothing was changed.",
        "Rows marked **fixture** look like test data from other seats.",
        "Currency: the record's own, else the customer's currency, else the company default; never converted or added "
        "across currencies. Rows by source: "
        + (", ".join(f"{k} {v}" for k, v in sorted(currency_sources(report).items())) or "none") + ".",
        "",
    ]
    for key, title in TITLES.items():
        sec = report["sections"][key]
        rows = sec["rows"]
        lines.append(f"## {title} ({len(rows) if sec['status'] == 'ok' else sec['status']})")
        lines.append("")
        if sec["status"] != "ok":
            lines += [f"**{sec['status'].upper()}:** {sec['reason']}", ""]
            continue
        lines += [f"Rule: {sec['rule']}.", ""]
        if key == "invoices_overdue":
            lines += _invoice_tables(rows, sec.get("no_due_date", []))
            continue
        if key == "orders_not_invoiced":
            # Compute from canonical rows as well for reports built by older callers.
            counts = order_age_summary(rows)
            lines += ["Age basis: **order date**, not a verified deliverable date. "
                      f"Deliverability unverified: **{len(rows)}** order(s), already included above; not an extra total.",
                      "Oldest first; equal ages grouped by currency (unknown last), then value descending within each currency.",
                      "Order-age buckets (exception rows only): "
                      + "; ".join(f"{label}: {count}" for label, count in counts.items()) + ".",
                      "The strict age > N filter still applies; buckets do not add younger orders.", ""]
            issues = sec.get("date_issues", [])
            if issues:
                lines += [f"### Orders with unusable dates ({len(issues)})", "",
                          "Excluded from age-based exception rows and bucket counts; review these date issues separately.", ""]
                lines += [f"- {cell(r.get('number') or r.get('id') or '(unnamed order)')}: {cell(r['reason'])}" for r in issues]
                lines += ["", "### Age-based exception rows", ""]
        if not rows:
            lines += ["None.", ""]
            continue
        if key == "orders_not_invoiced":
            lines += ["| Order | Customer | Date | Age (days) | Invoiced | Delivered | Total | |",
                      "|---|---|---|---:|---|---|---:|---|"]
            for r in rows:
                lines.append(f"| {cell(r['number'])} | {cell(r['customer'])} | {cell(r['order_date'])} | {r['age_days']} | "
                             f"{cell(r['invoiced_status'])} | {cell(r['delivered_status'])} | {_money(r['grand_total'])} {cell(_cur(r))} | "
                             f"{'fixture' if r['fixture'] else ''} |")
        else:
            lines += ["| Payment | Customer | Date | Age (days) | Amount | Unallocated | Status | |",
                      "|---|---|---|---:|---:|---:|---|---|"]
            for r in rows:
                lines.append(f"| {cell(r['number'])} | {cell(r['customer'])} | {cell(r['payment_date'])} | {r['age_days'] if r['age_days'] is not None else ''} | "
                             f"{_money(r['amount'])} | {_money(r['unallocated'])} {cell(_cur(r))} | {cell(r['status'])} | {'fixture' if r['fixture'] else ''} |")
        lines.append("")
    return "\n".join(lines)
