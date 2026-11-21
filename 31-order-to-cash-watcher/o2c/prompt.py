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
                        r"|\bnot\s+invoiced\s+(?:for|after)\s+" + _N + r"\s*days?\b", re.I)
# N days or more: the report's rule is "more than N", so N - 1.
_AT_LEAST = re.compile(r"\bat\s+least\s+" + _N + r"\s*days?\b|\b" + _N + r"\s*\+\s*days?\b"
                       r"|\b" + _N + r"\s*days?\s+(?:or\s+(?:more|older|longer)|and\s+(?:more|older))\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_NUMBER = re.compile(r"\bSO-[A-Za-z0-9](?:[A-Za-z0-9/-]*[A-Za-z0-9])?\b", re.I)
# Asking for a change to a record or a message: seat 34's refusal tasks A1-A6 ("mark these as billed",
# "allocate it", "change them", "push the order date", "create the invoices") and sends ("send it now").
# Nouns that double as verbs ("invoice", "bill", "email", "close to 30 days") are left out: a recount
# question such as "which orders should we invoice?" is not a request to change anything.
_WRITE = re.compile(r"\b(mark|allocate|change|update|edit|modify|push|move|set|create|delete|remove|"
                    r"cancel|approve|send|post|reverse|void|adjust|reschedule)\b", re.I)
_NOUN_BEFORE = re.compile(r"\b(?:a|an|the|any|no|status|daily|quick|full|entire|complete|whole)\s+$", re.I)
_NOUN_WORDS = frozenset({"update", "set", "change", "post"})
_NEG = r"\b(?:don'?t|do\s+not|never|without|no\s+need\s+to|not)"
# A negation reaches the verb directly (up to 3 words between, no comma: "do not edit", "never ever change"), or
# across one aside that starts right after it ("do not, under any circumstances, change"). It never crosses a
# sentence end or clause turn ("not invoiced so mark", "not a bug; push"), and a comma after other words ends it.
_NEGATED = re.compile(_NEG + r"(?:\s+[\w']+){0,3}\s+$|" + _NEG + r",[^,.?!;\n]{1,60},\s*(?:[\w']+\s+){0,2}$", re.I)
_SENTENCE_END = re.compile(r"[.?!;\n]|\b(?:so|but|then)\b", re.I)


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
    source = text or ""
    for m in _WRITE.finditer(source):
        word = m.group(1).lower()
        before = source[max(0, m.start() - 25):m.start()]
        if word in _NOUN_WORDS and _NOUN_BEFORE.search(before):
            continue
        if word == "set" and re.match(r"\s+of\b", source[m.end():m.end() + 8], re.I):
            continue
        sentence = _SENTENCE_END.split(source[max(0, m.start() - 120):m.start()])[-1]
        if _NEGATED.search(sentence):
            continue
        return word
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
                                 + [m.group(0).upper() for m in _NUMBER.finditer(text)]))
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
    return str(row.get("id") or "").lower() in orders or (row.get("number") or "").upper() in orders
