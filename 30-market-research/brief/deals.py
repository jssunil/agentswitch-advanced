"""Which deals the brief shows: open deals only, ranked by value in the base currency.

    ranked, issues = open_deals(rows)      # rows: every deal, from rows.from_record()
    shown = top(ranked, 10)

The rules are the "Selection rules" in PLAN.md; tasks 1-6 recount them from raw records, so a
change here needs a change there.

- Open: rows.stage_usable(row) and the stage is not closed_won / closed_lost.
- Every deal that is not closed is either ranked or left out with exactly one reason, in this
  order: unknown stage, value missing or unreadable, value <= 0 (judged on `value`), no usable
  base value or currency. A deal is never dropped silently.
- Ranked within one company and base currency, never across: base_value, largest first, then
  deal id as text, ascending ("D-10" before "D-9").
- Test data is ranked and flagged (fixture_flag), never hidden.

`issues` is [{deal_id, issue}]: the deal's own rows.py issues (for every deal that is not closed)
plus the reason a deal was left out. Closed deals carry no issues: the brief is about open deals.
"""
import re
from itertools import groupby

from .rows import replace, stage_usable
from .values import display_text

CLOSED = ("closed_won", "closed_lost")

LEFT_OUT_STAGE = "ranking: left out, the stage is unknown"
LEFT_OUT_VALUE_UNREADABLE = "value: missing or not readable, left out of the ranking"
LEFT_OUT_VALUE_NOT_POSITIVE = "value: zero or less, left out of the ranking"
LEFT_OUT_CURRENCY = "currency: no usable base value, left out of the ranking"
LEFT_OUT = (LEFT_OUT_STAGE, LEFT_OUT_VALUE_UNREADABLE, LEFT_OUT_VALUE_NOT_POSITIVE, LEFT_OUT_CURRENCY)

# Test data from other seats (PLAN.md, "Selection rules", test data). Judged on the title alone,
# so anyone can recount it from a Deal record. Ranked and flagged, never hidden.
FIXTURE_RULES = (
    ("test marker", re.compile(r"(?<![a-z])test(?![a-z])", re.IGNORECASE)),        # "[team99-test ...]"
    ("probe prefix", re.compile(r"^T\d+-", re.IGNORECASE)),                          # "T9-probe-..."
    ("generated suffix", re.compile(r"\s[—-]\s[0-9a-f]{8}$", re.IGNORECASE)),  # "Acme Ltd — 1a2b3c4d"
    ("generated title", re.compile(r"^Deal from [0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                                   re.IGNORECASE)),                                  # "Deal from <uuid>"
)


def fixture_reason(title):
    """The name of the first fixture rule the title matches, or None."""
    text = display_text(title or "").strip()
    for name, pattern in FIXTURE_RULES:
        if pattern.search(text):
            return name
    return None


def is_open(row):
    return stage_usable(row) and row.stage not in CLOSED


def left_out_reason(row):
    """Why a deal that is not closed is left out of the ranking, or None if it is ranked."""
    if not stage_usable(row):
        return LEFT_OUT_STAGE
    if row.value is None:
        return LEFT_OUT_VALUE_UNREADABLE
    if row.value <= 0:
        return LEFT_OUT_VALUE_NOT_POSITIVE
    if row.currency is None or row.base_currency is None or row.base_value is None or row.base_value <= 0:
        return LEFT_OUT_CURRENCY
    return None


def rank_key(row):
    """Group (company, base currency), then largest base value, then deal id as text."""
    return (row.company_id is not None, row.company_id or "", row.base_currency, -row.base_value, row.id)


def open_deals(rows):
    """(ranked, issues). `ranked` holds every eligible open deal in rank order, grouped by
    company and base currency; test data has fixture_flag set."""
    ranked, issues = [], []
    for row in rows:
        if stage_usable(row) and row.stage in CLOSED:
            continue
        issues.extend({"deal_id": row.id, "issue": i} for i in row.issues)
        reason = left_out_reason(row)
        if reason is not None:
            issues.append({"deal_id": row.id, "issue": reason})
            continue
        ranked.append(replace(row, fixture_flag=fixture_reason(row.title) is not None))
    ranked.sort(key=rank_key)
    return ranked, issues


def groups(ranked):
    """[(company_id, base_currency, [rows])] in rank order."""
    return [(k[0], k[1], list(g)) for k, g in groupby(ranked, key=lambda r: (r.company_id, r.base_currency))]


def _positive_int(n):
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError("n must be at least 1")


def top(ranked, n=10):
    """The first n deals of each company and base currency (all of them if there are fewer)."""
    _positive_int(n)
    return [row for _, _, rows in groups(ranked) for row in rows[:n]]


def tied_beyond(ranked, n=10):
    """{(company_id, base_currency): k}: k deals past the cut-off share the last shown deal's
    value, so the deal-id tie-break decided which were shown. Only groups with k > 0."""
    _positive_int(n)
    out = {}
    for company, currency, rows in groups(ranked):
        if len(rows) > n:
            k = sum(1 for r in rows[n:] if r.base_value == rows[n - 1].base_value)
            if k:
                out[(company, currency)] = k
    return out


__all__ = ["CLOSED", "LEFT_OUT", "LEFT_OUT_STAGE", "LEFT_OUT_VALUE_UNREADABLE", "LEFT_OUT_VALUE_NOT_POSITIVE",
           "LEFT_OUT_CURRENCY", "FIXTURE_RULES", "fixture_reason", "is_open", "left_out_reason", "rank_key",
           "open_deals", "groups", "top", "tied_beyond"]
