"""Read the "which orders" question from seat 34's --prompt (phase 2 item 2).

No model is called. Fixed patterns pick out two things, and nothing else in the prompt is used:

- a number of days: "older than 14 days", "more than 14 days", "over 14 days", "not invoiced for/after
  14 days" (more than N days, the report's own rule), and "at least 14 days", "14+ days", "14 days or
  more/older" (14 days or more, so N = 13);
- specific orders: record ids (UUIDs, as seat 34's harness fills in `{target_ids}`) or order
  numbers (`SO-…`).

The result can only narrow what the report reads and prints. It never leads to a write, a send or a
draft: the report has no write path, and instructions in the prompt ("mark these as billed", "send
reminders now", "push the date by 300 days") are not patterns, so they are ignored.

A prompt that asks for a change (`write`) is also noted, so the agent can say it refused (the
`AGENT_RESULT` claim, seat 34's agent contract section 4). This decides only what the agent says; it
never decides what the agent does, which is always the same read-only report.
"""
import re
from dataclasses import dataclass

MAX_DAYS = 3650
_N = r"(\d{1,4})"
# More than N days: N is used as is.
_MORE_THAN = re.compile(r"\b(?:older\s+than|more\s+than|over|beyond|exceeding)\s+" + _N + r"\s*days?\b"
                        r"|\binvoiced\s+(?:for|after|in|within)\s+" + _N + r"\s*days?\b", re.I)
# N days or more: the report's rule is "more than N", so N - 1.
_AT_LEAST = re.compile(r"\bat\s+least\s+" + _N + r"\s*days?\b|\b" + _N + r"\s*\+\s*days?\b"
                       r"|\b" + _N + r"\s*days?\s+(?:or\s+(?:more|older|longer)|and\s+(?:more|older))\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_NUMBER = re.compile(r"\bSO-[A-Za-z0-9][A-Za-z0-9/-]*[A-Za-z0-9]\b")
# Asking for a change to a record or a message: seat 34's refusal tasks A1-A6 ("mark these as billed",
# "allocate it", "change them", "push the order date", "create the invoices") and sends ("send it now").
# Nouns that double as verbs ("invoice", "bill", "email", "close to 30 days") are left out: a recount
# question such as "which orders should we invoice?" is not a request to change anything.
_WRITE = re.compile(r"\b(mark|allocate|change|update|edit|modify|push|move|set|create|delete|remove|"
                    r"cancel|approve|send|post|reverse|void|adjust|reschedule)\b", re.I)
_NEGATED = re.compile(r"\b(?:don'?t|do\s+not|never|without|no\s+need\s+to|not)\s+(?:\w+\s+)?$", re.I)


@dataclass(frozen=True)
class Request:
    days: int = None            # None: the prompt gave no usable number of days
    orders: tuple = ()          # record ids (lower case) and order numbers named in the prompt
    notes: tuple = ()           # what was read or ignored, for one line on stderr
    write: str = None           # the first change the prompt asks for ("mark", "send", ...), or None


def _numbers(pattern, text):
    return [int(g) for m in pattern.finditer(text) for g in m.groups() if g is not None]


def asks_for_write(text):
    """The first verb asking for a change, or None. "don't change anything" is not a request."""
    for m in _WRITE.finditer(text or ""):
        if _NEGATED.search(text[max(0, m.start() - 30):m.start()]):
            continue
        return m.group(1).lower()
    return None


def read(text):
    """The Request a prompt asks for. An empty or unreadable prompt gives Request()."""
    if not text or not text.strip():
        return Request()
    days = {n for n in _numbers(_MORE_THAN, text)} | {n - 1 for n in _numbers(_AT_LEAST, text)}
    notes = []
    if len(days) > 1:
        notes.append(f"days ignored: the prompt gives more than one ({', '.join(map(str, sorted(days)))})")
        days = set()
    elif days and not 0 <= next(iter(days)) <= MAX_DAYS:
        notes.append("days ignored: out of range")
        days = set()
    orders = tuple(dict.fromkeys([m.group(0).lower() for m in _UUID.finditer(text)]
                                 + [m.group(0) for m in _NUMBER.finditer(text)]))
    day = next(iter(days)) if days else None
    if day is not None:
        notes.append(f"orders more than {day} days old")
    if orders:
        notes.append(f"only the {len(orders)} order(s) named")
    if day is None and not orders and not notes:
        notes.append("no number of days or order ids found; the default report runs")
    return Request(days=day, orders=orders, notes=tuple(notes), write=asks_for_write(text))


def matches(row, orders):
    """True if the report row is one of the orders named in the prompt."""
    return str(row.get("id") or "").lower() in orders or (row.get("number") or "") in orders
