"""Account facts for each deal the brief shows (PLAN.md, "Account facts rules"; checked by task 7).

    data = load(client, rows)                  # one read of each list per run, not one per deal
    facts(client, row, as_of, data=data)       # -> dict (PLAN.md, "Interfaces", 3)

Task 7 recounts name, source, last_contact and open_activities from raw Party and Activity
records, so the rules below must match that table exactly:

- name: the account's (Party) name. Hidden from this seat (in the record's redacted fields): None,
  and "name" in `redacted`. No account, or one this seat can't see: name None, the other account
  fields None, `redacted` empty, `found` False, and the brief lists "account: not found".
- Cancelled activities are ignored completely, also when choosing the source.
- source "deal" when the deal has activities of its own; otherwise "party" when its account has
  activities of its own (on the account, with no deal); otherwise None. Another deal's activities
  never count for this deal. The same activities give last_contact and open_activities.
- last_contact: the latest done call, meeting or email (type trimmed, any letter case) dated on or
  before as_of; ties on the date go to the highest activity id, compared as text.
- open_activities: every activity not done, {id, subject, due_date, overdue}; overdue means due
  before as_of. A due date that isn't a date shows no due date and is not overdue (BUG-30-001).
- pinned_note: a pinned note on the deal, else a pinned note on the account itself (no deal);
  the latest updated wins, ties on the id as text. Only {id, title}: note content never goes
  into the brief.

Record text (names, subjects, titles) is passed on with control and invisible characters removed
and on one line; report.py escapes it for Markdown. Nothing here writes: only list reads.
"""
from .client import McpError
from .deals import is_open
from .values import business_date, currency_code, display_text, money

CONTACT_TYPES = frozenset({"call", "meeting", "email"})
ACCOUNT_NOT_FOUND = "account: not found"
# The brief's account fields and the Party fields they come from, for `redacted`.
_PARTY_FIELDS = {"name": ("name",), "type": ("type",), "city": ("addresses",), "state": ("addresses",),
                 "currency": ("currency_id",), "credit_limit": ("credit_limit",), "contacts": ("contacts",)}
SUBJECT_MAX = 120


def load(client, rows=None):
    """Everything facts() needs, read once: {parties {id: record}, activities, notes, relationships,
    rows}. Party and Activity are required (a failure stops the brief, as task 7 needs them). Notes
    and relationships are optional: a login that can't read them gets None, and the facts that
    need them say "not available" instead of guessing."""
    return {"parties": {str(p.get("id")): p for p in client.list_all("Party")
                        if isinstance(p, dict) and p.get("id") not in (None, "")},
            "activities": [a for a in client.list_all("Activity") if isinstance(a, dict)],
            "notes": _optional(client, "Note"),
            "relationships": _optional(client, "PartyRelationship"),
            "rows": list(rows) if rows is not None else None}


def facts(client, row, as_of, data=None):
    """The account facts for one deal on `as_of` (a date). `data` is load()'s result; without it
    the lists are read here (fine for one deal, slow for many)."""
    if data is None:
        data = load(client)
    as_of = business_date(as_of)
    party = data["parties"].get(row.party_id) if row.party_id else None
    found = party is not None
    hidden = set(party.get("_redacted_fields") or ()) if found else set()
    redacted = sorted(f for f, sources in _PARTY_FIELDS.items() if hidden & set(sources))

    def field(name, value):
        return None if not found or name in redacted else value

    address = _address(party) if found else {}
    pool, source = _activities(row, data["activities"])
    return {
        "party_id": row.party_id,
        "found": found,
        # display_text only, not _one_line: task 7 compares the name exactly; report.py makes it one line.
        "name": field("name", _text(party.get("name")) if found else None),
        "type": field("type", _one_line(party.get("type")) if found else None),
        "city": field("city", address.get("city")),
        "state": field("state", address.get("state")),
        "currency": field("currency", currency_code(party.get("currency_id")) if found else None),
        "credit_limit": field("credit_limit", _money_text(party.get("credit_limit")) if found else None),
        "contacts": field("contacts", _contacts(row, party, data)) if found else [],
        "other_open_deals": _other_open_deals(row, data.get("rows")),
        "last_contact": _last_contact(pool, as_of),
        "open_activities": _open_activities(pool, as_of),
        "pinned_note": _pinned_note(row, data.get("notes")),
        "redacted": redacted,
        "source": source,
    }


def issues(row, account):
    """Data issues the account facts add for one deal: [{deal_id, issue}]."""
    if isinstance(account, dict) and not account.get("found"):
        return [{"deal_id": row.id, "issue": ACCOUNT_NOT_FOUND}]
    return []


def contact_type(value):
    """An activity type as the rules compare it: trimmed, lower case; None if it isn't text."""
    return value.strip().lower() if isinstance(value, str) else None


# ---------------------------------------------------------------- helpers

def _optional(client, entity):
    try:
        return [r for r in client.list_all(entity) if isinstance(r, dict)]
    except (McpError, KeyError):
        return None


def _activities(row, activities):
    """(the activities that count for this deal, source)."""
    usable = [a for a in activities if a.get("cancelled") is not True]
    own = [a for a in usable if row.id and _id(a.get("deal_id")) == row.id]
    if own:
        return own, "deal"
    if row.party_id:
        account = [a for a in usable if _id(a.get("party_id")) == row.party_id and not a.get("deal_id")]
        if account:
            return account, "party"
    return [], None


def _last_contact(pool, as_of):
    best = None
    for a in pool:
        kind = contact_type(a.get("type"))
        if a.get("done") is not True or kind not in CONTACT_TYPES:
            continue
        day = _day(a.get("due_date"))
        if day is None or day > as_of:
            continue
        key = (day, str(a.get("id")))
        if best is None or key > best[0]:
            best = (key, {"date": day.isoformat(), "type": kind, "subject": _subject(a)})
    return None if best is None else best[1]


def _open_activities(pool, as_of):
    out = []
    for a in pool:
        if a.get("done") is True:
            continue
        day = _day(a.get("due_date"))
        out.append({"id": str(a.get("id")), "subject": _subject(a),
                    "due_date": None if day is None else day.isoformat(),
                    "overdue": day is not None and day < as_of})
    return sorted(out, key=lambda x: (x["due_date"] is None, x["due_date"] or "", x["id"]))


def _contacts(row, party, data):
    """People for this account: the deal's own contact first, then people linked to the account
    by an active relationship ("represents"). [{id, name, role}]; a person this seat can't see
    shows name None."""
    parties, out, seen = data["parties"], [], set()

    def add(person_id, role):
        if person_id and person_id not in seen:
            seen.add(person_id)
            person = parties.get(person_id)
            hidden = set((person or {}).get("_redacted_fields") or ())
            name = None if person is None or "name" in hidden else _one_line(person.get("name"))
            out.append({"id": person_id, "name": name, "role": role})

    add(row.contact_id, "deal contact")
    links = data.get("relationships") or ()
    for r in sorted(links, key=lambda r: (not r.get("is_primary"), str(r.get("id")))):
        if _id(r.get("to_party_id")) == row.party_id and r.get("is_active") is not False:
            add(_id(r.get("from_party_id")), _one_line(r.get("relationship")) or "linked")
    return out


def _other_open_deals(row, rows):
    """The account's other open deals, [{id, title, stage}] by id; None when the rows weren't given."""
    if rows is None:
        return None
    if not row.party_id:
        return []
    return [{"id": r.id, "title": r.title, "stage": r.stage_label or r.stage}
            for r in sorted(rows, key=lambda r: r.id)
            if r.party_id == row.party_id and r.id != row.id and is_open(r)]


def _pinned_note(row, notes):
    if notes is None:
        return None
    pinned = [n for n in notes if n.get("pinned") is True]
    pool = [n for n in pinned if _id(n.get("deal_id")) == row.id]
    if not pool and row.party_id:
        pool = [n for n in pinned if _id(n.get("party_id")) == row.party_id and not n.get("deal_id")]
    if not pool:
        return None
    note = max(pool, key=lambda n: (str(n.get("updated_at") or ""), str(n.get("id"))))
    return {"id": str(note.get("id")), "title": _one_line(note.get("title")) or "untitled"}


def _address(party):
    """City and state from the first address that has either."""
    for a in party.get("addresses") or ():
        if isinstance(a, dict) and (a.get("city") or a.get("state")):
            return {"city": _one_line(a.get("city")), "state": _one_line(a.get("state"))}
    return {}


def _subject(activity):
    text = _one_line(activity.get("subject")) or "untitled"
    return text if len(text) <= SUBJECT_MAX else text[:SUBJECT_MAX - 1] + "…"


def _one_line(value):
    if value is None or value == "":
        return None
    return " ".join(display_text(value).split()) or None


def _text(value):
    return None if value is None or value == "" else display_text(value)


def _money_text(value):
    try:
        amount = money(value)
    except ValueError:
        return None
    return None if amount is None else str(amount)


def _day(value):
    try:
        return business_date(value)
    except (ValueError, TypeError):
        return None


def _id(value):
    return None if value in (None, "") else str(value)


__all__ = ["CONTACT_TYPES", "ACCOUNT_NOT_FOUND", "load", "facts", "issues", "contact_type"]
