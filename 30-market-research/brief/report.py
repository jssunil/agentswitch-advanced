"""The brief: the largest open deals, with account facts, what changed and one next step each.

    out = build(client, "suryodaya", date(2026, 11, 30))     # PLAN.md, "Interfaces", 6
    text = to_markdown(out)

build() reads the platform (list reads only; the client refuses anything else), ranks with
deals.py, adds accounts.facts(), compares with last week's snapshot and saves this week's
(changes.py), and asks nextstep.suggest() for one step per deal. It never writes to the platform.

`prompt` (the command line's --prompt, and what seat 34's harness appends) only picks deals: a
number ("top 5") or deal ids named in it. Nothing in it is followed as an instruction. A prompt
that asks for a change (close, move, log, update...) is answered with the same read-only brief
and a note that a person makes any change (`prompt.refused`).

Record text (titles, names, subjects) reaches the Markdown only through md(), which removes
control and invisible characters, keeps it on one line and escapes Markdown, so a deal title
can't add a heading, a link or a table column (an e-mail address still shows as a mailto link).
"""
import json
import os
import re
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import accounts, changes, deals, nextstep
from .rows import from_record, stage_labels, stage_usable, to_dict
from .values import business_date, display_text, money_text

DEFAULT_TOP = 10
MAX_TOP = 100
IST = timezone(timedelta(hours=5, minutes=30))
BRIEFS_FOLDER = changes.SEAT_FOLDER / "briefs"
NO_ID = "deal: record without an id, not read"
OTHER_LOGIN_LINE = ("compared with a snapshot taken by another login: some changes may only be "
                    "differences in what each login can see")

# --prompt: what it can pick, and the words that ask for a change (never followed).
_TOP = re.compile(r"\btop\s+(\d+)\b|\b(\d+)\s+(?:largest|biggest|top)\b", re.IGNORECASE)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
# A change word counts only as an instruction: the first word of a sentence or clause, after any
# polite lead-in ("please", "can you", "for now", "go ahead and"...). So "Move these to won",
# "...and log a call" and "Can you mark D-2 as won?" are refused, while a noun ("any change in stage",
# "change since last week", "the call log"), a question ("what should I close first?") or a negated
# list ("Do not merge, delete, or change any record") is not.
_CHANGE_VERBS = frozenset({"move", "mark", "log", "update", "change", "edit", "modify", "set", "push",
                           "create", "delete", "remove", "cancel", "merge",
                           "send", "assign", "reassign", "schedule", "book", "close"})
_NEGATED_VERBS = re.compile(
    r"\b(?:do\s+not|don['’]?t|never|without|not\s+to)\s+(?:ever\s+)?"
    r"[a-z]+(?:ing)?(?:\s*,\s*(?:(?:or|and)\s+)?[a-z]+(?:ing)?)*(?:\s+(?:or|and)\s+[a-z]+(?:ing)?)?\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK = re.compile(r"[.;:!?,\n]+|\b(?:and|then|also|but|so)\b", re.IGNORECASE)
_LEAD_IN = re.compile(
    r"^(?:please|kindly|just|now|for now|right now|quick(?:ly)?|ok(?:ay)?|hey|hi|well|actually|first|"
    r"new updates?|go ahead and|let'?s|let us|"
    r"(?:can|could|would|will) you(?: please)?|"
    r"i(?: want| need| would like|'d like) you to)\b\s*",
    re.IGNORECASE,
)
# Clause starts that use a change word without asking to change a record.
_NOT_A_CHANGE = re.compile(
    r"^(?:update|send|brief)\s+(?:me|us|on)\b"       # asks for information: "update me on...", "update on..."
    r"|^close\s+(?:dates?|to)\b"                     # the field or distance: "close date first", "close to 10M"
    r"|^change\s+(?:in|since|from|between)\b"        # a noun phrase: "change in stage", "change since last week"
    r"|^[a-z]+\s+of\b"                               # a noun: "change of plan", "log of calls", "set of deals"
    r"|^[a-z]+['’]s\b"                               # a name: "Mark's deals"
    r"|^(?:remove|hide|drop)\b.*\bfrom\s+(?:the|this|my)\s+(?:list|brief|report|table|summary)\b",
    re.IGNORECASE,
)
# What a deal id looks like on the platform, to report one that is named but not found.
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def change_request(text):
    """The first change word used as an instruction in `text`, lower case, or None."""
    cleaned = _NEGATED_VERBS.sub("keep", str(text or ""))
    for clause in _CLAUSE_BREAK.split(cleaned):
        clause = clause.strip().lstrip("\"'“”‘’([").strip()
        while True:
            shorter = _LEAD_IN.sub("", clause, count=1)
            if shorter == clause:
                break
            clause = shorter
        first = re.match(r"[A-Za-z]+", clause)
        if first and first.group(0).lower() in _CHANGE_VERBS and not _NOT_A_CHANGE.search(clause):
            return first.group(0).lower()
    return None


def today_ist():
    return datetime.now(IST).date()


def read_prompt(prompt, deal_ids=()):
    """{top, deals, refused, notes} from the --prompt text. `deals` are the deal ids named in it
    (whole words), in the order named: ids in `deal_ids`, and any other word shaped like a
    platform deal id (build() notes those as not found); `top` a number asked for (1-MAX_TOP)
    or None; `refused` the first change word used as an instruction (change_request), or None."""
    out = {"top": None, "deals": [], "refused": None, "notes": []}
    if not prompt:
        return out
    text = str(prompt)
    m = _TOP.search(text)
    if m:
        n = int(m.group(1) or m.group(2))
        if 1 <= n <= MAX_TOP:
            out["top"] = n
        else:
            out["notes"].append(f"a top of {n} is outside 1-{MAX_TOP}; the default is used")
    known = set(deal_ids)
    uuid_by_lower = {d.lower(): d for d in deal_ids if _UUID.match(d)}
    for token in _TOKEN.findall(text):
        resolved = token if token in known else (uuid_by_lower.get(token.lower(), token)
                                                 if _UUID.match(token) else None)
        if resolved is not None and resolved not in out["deals"]:
            out["deals"].append(resolved)
    w = change_request(text)
    if w:
        out["refused"] = w
        out["notes"].append(f"the prompt asks to {out['refused']} records; this brief only reads and "
                            "suggests. A person makes any change in the CRM")
    return out


def _not_shown(named, shown_ids, rows):
    """One note per deal the prompt named that the brief doesn't show: closed, left out of the
    ranking (with the reason), or not found (no such deal, or one this login can't see)."""
    by_id = {r.id: r for r in rows}
    notes = []
    for deal_id in named:
        if deal_id in shown_ids:
            continue
        row = by_id.get(deal_id)
        if row is None:
            why = "not found (no such deal, or one this login can't see)"
        elif stage_usable(row) and row.stage in deals.CLOSED:
            why = changes.CLOSED.get(row.stage, "closed")
        else:
            why = deals.left_out_reason(row) or "not ranked"
        notes.append(f"deal {deal_id} is not shown: {why}")
    return notes


def build(client, instance, as_of, top=None, since=None, prompt=None, snapshots=None):
    """The brief as a JSON-safe dict (PLAN.md, "Interfaces", 6):

    {instance, date, since, baseline, previous_date, other_login, top, prompt, groups, deals
     [{rank, row, account, changes, next_step, test_data}], entered, dropped, data_issues,
     data_issue_deals, left_out, unreadable_records, test_data_shown, snapshot}

    `top` defaults to a number in the prompt, else 10; it counts per company and base currency.
    `snapshots` is the snapshot folder; without it changes.snapshot_folder() picks one (the
    official weekly folder only for a plain run dated today). Raises changes.NoSnapshot for a
    `since` with no snapshot, changes.SnapshotError for a damaged one, client.McpError when the
    platform can't be read."""
    as_of = _day(as_of)
    since = None if since is None else _day(since)
    labels = stage_labels(client.list_all("Pipeline"))
    rows, unreadable = [], 0
    for record in client.list_all("Deal"):
        try:
            rows.append(from_record(record, labels))
        except ValueError:
            unreadable += 1
    ranked, issues = deals.open_deals(rows)

    asked = read_prompt(prompt, [r.id for r in rows])
    n = top if top is not None else (asked["top"] or DEFAULT_TOP)
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= MAX_TOP:
        raise ValueError(f"top must be a whole number from 1 to {MAX_TOP}")
    named = set(asked["deals"])
    shown = [r for r in ranked if r.id in named] if named else deals.top(ranked, n)
    asked["notes"][:0] = _not_shown(asked["deals"], {r.id for r in shown}, rows)
    # rank: the deal's place in its company and base currency, also for a hand-picked list
    place = {r.id: i for _, _, group in deals.groups(ranked) for i, r in enumerate(group, 1)}

    data = accounts.load(client, rows)
    login = changes.login_ref(client)
    today = today_ist()
    folder = changes.snapshot_folder(snapshots, date_given=as_of != today, prompt=prompt)
    # A prompt that names deals shows a hand-picked list: last week's top N isn't comparable, so
    # only the per-deal changes are reported, not entered or dropped deals.
    compared = changes.compare(shown, folder, instance, as_of, all_rows=rows, top=None if named else n,
                               since=since, login=login)
    official = Path(folder).resolve() == changes.OFFICIAL_FOLDER.resolve()
    saved = changes.save_snapshot(shown, instance, as_of, folder, ranked=ranked,
                                  not_after=today if official else None, login=login)

    entries, rank_in_group = [], Counter()
    for row in shown:
        key = (row.company_id, row.base_currency)
        rank_in_group[key] += 1
        account = accounts.facts(client, row, as_of, data=data)
        issues.extend(accounts.issues(row, account))
        entries.append({"rank": place[row.id], "row": to_dict(row), "account": account,
                        "changes": compared["per_deal"].get(row.id, []),
                        "next_step": nextstep.suggest(row, account, as_of),
                        "test_data": deals.fixture_reason(row.title)})

    tied = {} if named else deals.tied_beyond(ranked, n)
    groups = [{"company_id": company, "base_currency": currency, "eligible": len(group),
               "shown": rank_in_group.get((company, currency), 0),
               "tied_beyond": tied.get((company, currency), 0),
               "test_data_shown": sum(1 for e in entries if e["test_data"]
                                      and (e["row"]["company_id"], e["row"]["base_currency"]) == (company, currency))}
              for company, currency, group in deals.groups(ranked)]
    left_out = Counter(i["issue"] for i in issues if i["issue"] in deals.LEFT_OUT)
    return {
        "instance": instance, "date": as_of.isoformat(), "since": None if since is None else since.isoformat(),
        "baseline": compared["baseline"], "previous_date": compared["previous_date"],
        "other_login": compared.get("other_login"), "top": n,
        "prompt": None if prompt is None else {k: asked[k] for k in ("top", "deals", "refused", "notes")},
        "groups": groups, "deals": entries,
        "entered": [] if named else compared["entered"], "dropped": [] if named else compared["dropped"],
        "data_issues": issues, "data_issue_deals": len({i["deal_id"] for i in issues}),
        "left_out": {reason: left_out[reason] for reason in deals.LEFT_OUT if left_out[reason]},
        "unreadable_records": unreadable,
        "test_data_shown": sum(1 for e in entries if e["test_data"]),
        "snapshot": str(saved),
    }


# ---------------------------------------------------------------- Markdown

_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]<>#|~!])")
# GitHub turns bare "https://..." and "www...." into links; a backslash stops it and isn't shown.
_AUTOLINK = re.compile(r"(://|(?<![A-Za-z0-9])www\.)", re.IGNORECASE)


def md(value, empty="—"):
    """Record text, safe in Markdown: no control or invisible characters, one line, special
    characters escaped, and "://" and "www." broken (so it can't make a heading, link, image,
    HTML or a table column). An e-mail address still shows as a mailto link on GitHub, which no
    escape stops; it opens a mail program, never a page."""
    if value is None or value == "":
        return empty
    text = " ".join(display_text(value).split())
    if not text:
        return empty
    text = _MD_SPECIAL.sub(r"\\\1", text)
    return _AUTOLINK.sub(lambda m: "\\://" if m.group(1) == "://" else m.group(1)[:3] + "\\.", text)


def quoted(value):
    """CRM text shown as a quotation inside our own wording (rule 2's subject)."""
    return f"“{md(value, 'untitled')}”"


def to_markdown(brief):
    b = brief
    lines = [f"# Deal brief: {md(b['instance'])}, {b['date']}", ""]
    lines += _header(b)
    for g in b["groups"]:
        group_deals = [d for d in b["deals"]
                       if (d["row"]["company_id"], d["row"]["base_currency"]) == (g["company_id"], g["base_currency"])]
        if not group_deals:
            continue
        lines += ["", f"## Company {md(g['company_id'])}, values in {md(g['base_currency'])}", ""]
        lines += _group_notes(b, g)
        lines += ["| # | Deal | Account | Stage | Value | Close | Next step |",
                  "|---:|---|---|---|---:|---|---|"]
        for d in group_deals:
            r = d["row"]
            title = md(r["title"]) + (" *(looks like test data)*" if d["test_data"] else "")
            lines.append(f"| {d['rank']} | {title} | {md(d['account'].get('name') if d['account'].get('found') else None)} "
                         f"| {md(r['stage_label'] or r['stage'])} | {money_text(r['base_value'])} {md(r['base_currency'], '')} "
                         f"| {r['expected_close_date'] or 'none'} | {md(d['next_step']['step'])} |")
        for d in group_deals:
            lines += [""] + _deal_detail(b, d)
    if not b["deals"]:
        named = b["prompt"] and b["prompt"]["deals"]
        lines += ["", "None of the deals the request named is a ranked open deal (see the notes above)." if named
                  else "No open deal could be ranked."]
    lines += _dropped(b) + _issues(b)
    lines += ["", "---", "Read-only: this brief changed nothing in the CRM. Next steps are suggestions for a person "
              "to act on."]
    return "\n".join(lines) + "\n"


def _header(b):
    out = []
    if b["since"]:
        out.append(f"- What changed: compared with the snapshot of {b['since']}.")
    elif b["baseline"]:
        out.append("- What changed: **baseline**. This is the first run with no earlier snapshot, so nothing "
                   "is reported as changed.")
    else:
        out.append(f"- What changed: since the previous run on {b['previous_date']}.")
    if b["other_login"]:
        out.append(f"- Note: {OTHER_LOGIN_LINE}.")
    shown = len(b["deals"])
    if b["test_data_shown"]:
        out.append(f"- {b['test_data_shown']} of the {shown} deals shown look like test data (flagged, not hidden).")
    if b["prompt"]:
        p = b["prompt"]
        if p["deals"]:
            out.append(f"- Showing the {shown} open deal(s) the request named, of {len(p['deals'])} named.")
        for note in p["notes"]:
            out.append(f"- Request: {md(note)}.")
    out.append(f"- Top {b['top']} per company and base currency; values are in the base currency, never "
               "added across currencies.")
    if b["data_issue_deals"] or b["unreadable_records"]:
        parts = []
        if b["data_issue_deals"]:
            parts.append(f"{b['data_issue_deals']} deals have data issues")
        if b["unreadable_records"]:
            parts.append(f"{b['unreadable_records']} deal record(s) without an id were not read")
        out.append(f"- {'; '.join(parts)} (listed at the end).")
    return out


def _group_notes(b, g):
    out = []
    if g["eligible"] < b["top"] and not (b["prompt"] and b["prompt"]["deals"]):
        out.append(f"Only {g['eligible']} eligible deals.")
    if g["tied_beyond"]:
        out.append(f"{g['tied_beyond']} more deal(s) share the value of deal {g['shown']} at the cut-off; "
                   "the deal id decided which are shown.")
    return out + ([""] if out else [])


def _deal_detail(b, d):
    r, a, step = d["row"], d["account"], d["next_step"]
    out = [f"### {d['rank']}. {md(r['title'])}", ""]
    if a.get("found"):
        place = ", ".join(md(x) for x in (a.get("city"), a.get("state")) if x)
        acct_empty = "not available to this seat" if "name" in (a.get("redacted") or ()) else "—"
        facts = [f"**Account:** {md(a.get('name'), acct_empty)}"
                 + (f" ({md(a.get('type'))})" if a.get("type") else "") + (f", {place}" if place else "")]
        if a.get("credit_limit") is not None:
            facts.append(f"credit limit {money_text(a['credit_limit'])} {md(a.get('currency'), '')}".rstrip())
        out.append("- " + "; ".join(facts))
        if a.get("redacted"):
            out.append(f"- Not available to this seat: {', '.join(md(f) for f in a['redacted'])}")
    else:
        out.append("- **Account:** not found (no account on the deal, or one this seat can't see)")
    people = [f"{md(c.get('name'), 'not available to this seat')} ({md(c.get('role'))})" for c in a.get("contacts") or ()]
    if people:
        out.append(f"- Contacts: {', '.join(people)}")
    others = a.get("other_open_deals")
    if others:
        out.append(f"- Other open deals on this account: {len(others)}")
    lc = a.get("last_contact")
    out.append(f"- Last contact: {lc['date']}, {md(lc['type'])}, {quoted(lc.get('subject'))}" if lc
               else "- Last contact: none recorded")
    acts = a.get("open_activities") or []
    if acts:
        overdue = sum(1 for x in acts if x.get("overdue"))
        out.append(f"- Open activities: {len(acts)}" + (f", {overdue} overdue" if overdue else ""))
        for x in acts[:5]:
            due = x.get("due_date") or "no due date"
            out.append(f"  - {due}{' (overdue)' if x.get('overdue') else ''}: {quoted(x.get('subject'))}")
        if len(acts) > 5:
            out.append(f"  - and {len(acts) - 5} more")
    if a.get("pinned_note"):
        out.append(f"- Pinned note: {quoted(a['pinned_note'].get('title'))}")
    if d["changes"]:
        out.append("- What changed: " + "; ".join(_change(c) for c in d["changes"]))
    elif not b["baseline"]:
        out.append("- What changed: " + ("new in this list" if r["id"] in b["entered"] else "nothing"))
    out.append(f"- **Next step:** {md(step['step'])}. *Why:* {md(step['reason'])}.")
    if step.get("activity"):
        act = step["activity"]
        out.append(f"  - Overdue activity (CRM text): {quoted(act.get('subject'))}, due {act.get('due_date')}")
    return out


def _change(c):
    money_fields = ("value", "base_value", "probability")
    fmt = (lambda v: money_text(v)) if c["field"] in money_fields else (lambda v: md(v, "none"))
    text = f"{md(c['field'])} {fmt(c['before'])} → {fmt(c['after'])}"
    return text + (f" ({md(c['cause'])})" if c.get("cause") else "")


def _dropped(b):
    if not b["dropped"]:
        return []
    out = ["", "## Left the list since last time", ""]
    out += [f"- {md(x['title'])}: {md(x['reason'])}" for x in b["dropped"]]
    return out


def _issues(b):
    if not b["data_issues"] and not b["unreadable_records"]:
        return []
    counts = []
    if b["data_issue_deals"] or not b["unreadable_records"]:
        counts.append(f"{b['data_issue_deals']} deals")
    if b["unreadable_records"]:
        counts.append(f"{b['unreadable_records']} unreadable record(s)")
    out = ["", f"## Data issues ({', '.join(counts)})", ""]
    if b["left_out"]:
        out.append("Left out of the ranking: " + "; ".join(f"{md(k)}: {v}" for k, v in b["left_out"].items()) + ".")
        out.append("")
    by_deal = {}
    for i in b["data_issues"]:
        by_deal.setdefault(i["deal_id"], []).append(i["issue"])
    for deal_id in sorted(by_deal):
        out.append(f"- `{md(deal_id)}`: {'; '.join(md(x) for x in by_deal[deal_id])}")
    if b["unreadable_records"]:
        out.append(f"- {b['unreadable_records']} deal record(s) without an id were not read")
    return out


# ---------------------------------------------------------------- files

def write(brief, markdown, out=None):
    """Write <instance>-<date>.json and .md (owner-only) to `out`, default the seat's gitignored
    briefs/ folder (anchored to the seat folder, like the snapshots). Returns the two paths."""
    folder = Path(out) if out else BRIEFS_FOLDER
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    stem = f"{changes._instance(brief['instance'])}-{_day(brief['date']).isoformat()}"
    encoded = json.dumps(brief, indent=1, ensure_ascii=False, allow_nan=False)
    paths = []
    for suffix, text in ((".json", encoded), (".md", markdown)):
        target = folder / (stem + suffix)
        if target.is_symlink():
            raise ValueError(f"{target.name}: the brief must not be written through a symlink")
        fd, temporary = tempfile.mkstemp(prefix=".brief-", suffix=suffix, dir=folder)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:     # mode 0600: customer data
                f.write(text)
            os.replace(temporary, target)
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise
        paths.append(target)
    return paths


def _day(value):
    day = business_date(value)
    if day is None:
        raise ValueError("a date is needed")
    return day


__all__ = ["DEFAULT_TOP", "MAX_TOP", "BRIEFS_FOLDER", "OTHER_LOGIN_LINE", "read_prompt", "build", "to_markdown",
           "md", "write", "today_ist"]
