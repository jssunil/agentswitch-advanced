"""Why one deal is shown or left out, its account facts, and why it gets its next step.

    python3 -m brief --explain <deal-id> [--instance {suryodaya,keystone}] [--date YYYY-MM-DD]
    python3 -m brief --explain <deal-id> --replay briefs/recordings/keystone-2026-11-25

Reads what report.build() reads (Pipeline, Deal, Party, Activity, plus optional Note and
PartyRelationship), so a suite or brief recording answers it offline with no platform or token.
Selection rules are walked in order and cross-checked against deals.open_deals() and deals.top();
account facts come from accounts.facts(); the next-step rule trace comes from nextstep.explain().

For a person debugging: prints customer data on stdout, writes no snapshot or brief file, and never
prints the deal ID list or AGENT_RESULT. Exit 0 when found, 5 when not found (or ambiguous prefix),
2 on bad options, 3 when the platform or recording cannot be read.
"""
import sys

from . import accounts, deals, nextstep, report
from .rows import (PIPELINE_MISSING, PIPELINE_NOT_FOUND, STAGE_NOT_IN_PIPELINE,
                   from_record, stage_labels, stage_usable)
from .values import business_date, display_text, money_text

EXIT_NOT_FOUND = 5
PREFIX_MIN = 4


def _one_line(value, empty="(none)"):
    if value is None or value == "":
        return empty
    text = " ".join(display_text(value).split())
    return text if text else empty


def _money_pair(amount, currency):
    if amount is None:
        return f"(missing or unreadable) {_one_line(currency, '')}".rstrip()
    return f"{money_text(amount)} {_one_line(currency, '(no currency)')}"


def find_deal(rows, wanted):
    """(row or None, matches, how). Exact id match wins; otherwise a unique prefix of 4+ chars
    (tasks_brief shortens deal ids to 8 characters in failure evidence)."""
    exact = [r for r in rows if r.id == wanted]
    if exact:
        return exact[0], exact, "exact id"
    if len(wanted) >= PREFIX_MIN:
        pref = [r for r in rows if r.id.startswith(wanted)]
        if len(pref) == 1:
            return pref[0], pref, f"id prefix {_one_line(wanted)}"
        if len(pref) > 1:
            return None, pref, "ambiguous prefix"
    return None, [], "not found"


def _stage_detail(row):
    if PIPELINE_MISSING in row.issues:
        return PIPELINE_MISSING
    if PIPELINE_NOT_FOUND in row.issues:
        return PIPELINE_NOT_FOUND
    if STAGE_NOT_IN_PIPELINE in row.issues:
        return STAGE_NOT_IN_PIPELINE
    if row.stage is None:
        return "stage: missing"
    return "stage check"


def actual_verdict(row, ranked, shown):
    shown_ids = {r.id for r in shown}
    ranked_ids = {r.id for r in ranked}
    if row.id in shown_ids:
        return "shown"
    if row.id in ranked_ids:
        return "outranked"
    if stage_usable(row) and row.stage in deals.CLOSED:
        return "closed"
    return "left out"


def walk_selection(row, all_rows, top=report.DEFAULT_TOP):
    """(steps [(state, text)], walked_verdict, actual_verdict, group_info, deal_issues).
    `state` is 'yes', 'NO', or '---' (not reached)."""
    ranked, all_issues = deals.open_deals(all_rows)
    shown = deals.top(ranked, top)
    actual = actual_verdict(row, ranked, shown)
    deal_issues = [i["issue"] for i in all_issues if i["deal_id"] == row.id]

    steps = []
    usable = stage_usable(row)
    if usable:
        if row.stage_known:
            steps.append(("yes", f"usable stage: {_one_line(row.stage)} ({_one_line(row.stage_label)}) "
                                 f"in pipeline {_one_line(row.pipeline)}"))
        else:
            steps.append(("yes", f"usable stage: {_one_line(row.stage)} judged by stage key "
                                 f"({_stage_detail(row)}; OBS-30-001)"))
    else:
        steps.append(("NO", f"usable stage: {_one_line(row.stage)} in pipeline {_one_line(row.pipeline)} "
                            f"({_stage_detail(row)}) -> {deals.LEFT_OUT_STAGE}"))
        for rest in ("open stage", "readable value", "positive value", "usable base currency and base value",
                     f"within top {top} of group"):
            steps.append(("---", f"{rest}: not reached"))
        return steps, "left out", actual, None, deal_issues

    if row.stage in deals.CLOSED:
        steps.append(("NO", f"open stage: stage is {_one_line(row.stage)} (closed; omitted with no data issues)"))
        for rest in ("readable value", "positive value", "usable base currency and base value",
                     f"within top {top} of group"):
            steps.append(("---", f"{rest}: not reached"))
        return steps, "closed", actual, None, deal_issues
    steps.append(("yes", f"open stage: {_one_line(row.stage)} is not closed_won or closed_lost"))

    if row.value is None:
        steps.append(("NO", f"readable value: missing or unreadable -> {deals.LEFT_OUT_VALUE_UNREADABLE}"))
        for rest in ("positive value", "usable base currency and base value", f"within top {top} of group"):
            steps.append(("---", f"{rest}: not reached"))
        return steps, "left out", actual, None, deal_issues
    steps.append(("yes", f"readable value: {_money_pair(row.value, row.currency)}"))

    if row.value <= 0:
        steps.append(("NO", f"positive value: {money_text(row.value)} <= 0 (judged on deal's own value) "
                            f"-> {deals.LEFT_OUT_VALUE_NOT_POSITIVE}"))
        for rest in ("usable base currency and base value", f"within top {top} of group"):
            steps.append(("---", f"{rest}: not reached"))
        return steps, "left out", actual, None, deal_issues
    steps.append(("yes", f"positive value: {money_text(row.value)} > 0"))

    cur_ok = (row.currency is not None and row.base_currency is not None
              and row.base_value is not None and row.base_value > 0)
    if not cur_ok:
        steps.append(("NO", f"usable base currency and base value: currency={_one_line(row.currency)}, "
                            f"base={_money_pair(row.base_value, row.base_currency)} -> {deals.LEFT_OUT_CURRENCY}"))
        steps.append(("---", f"within top {top} of group: not reached"))
        return steps, "left out", actual, None, deal_issues
    steps.append(("yes", f"usable base currency and base value: {money_text(row.base_value)} {row.base_currency} "
                         f"(deal currency {row.currency})"))

    group = next((g for c, cur, g in deals.groups(ranked)
                  if (c, cur) == (row.company_id, row.base_currency)), [])
    place = next((i for i, r in enumerate(group, 1) if r.id == row.id), None)
    peers = [r.id for r in group if r.id != row.id and r.base_value == row.base_value]
    tie_note = (f"; tied on base_value with {len(peers)} deal(s), ordered by deal id as text"
                if peers else "")
    tied_cut = deals.tied_beyond(ranked, top).get((row.company_id, row.base_currency), 0)
    info = {"company_id": row.company_id, "base_currency": row.base_currency,
            "place": place, "eligible": len(group), "tied_beyond": tied_cut}
    if place is not None and place <= top:
        steps.append(("yes", f"within top {top} of ({_one_line(row.company_id)}, {row.base_currency}): "
                             f"rank #{place} of {len(group)}{tie_note}"))
        return steps, "shown", actual, info, deal_issues

    cutoff = money_text(group[top - 1].base_value) if len(group) >= top else "(none)"
    at_cut = " (shares cut-off value; deal-id tie-break decided)" \
        if len(group) >= top and row.base_value == group[top - 1].base_value else ""
    steps.append(("NO", f"within top {top} of ({_one_line(row.company_id)}, {row.base_currency}): "
                        f"rank #{place} of {len(group)}; #{top} has {cutoff} {row.base_currency}{at_cut}"))
    return steps, "outranked", actual, info, deal_issues


def describe_deal(row, how, all_rows, acct, as_of, top=report.DEFAULT_TOP):
    """Lines of plain, control-safe text explaining `row`."""
    as_of = business_date(as_of)
    steps, walked, actual, info, deal_issues = walk_selection(row, all_rows, top)
    acct_issues = [i["issue"] for i in accounts.issues(row, acct)] if actual == "shown" else []
    fixture = deals.fixture_reason(row.title)
    ns = nextstep.explain(row, acct, as_of)

    lines = [
        f"Deal {_one_line(row.id)} ({how}): {_one_line(row.title)}",
        f"  Company: {_one_line(row.company_id)} | Pipeline: {_one_line(row.pipeline)} | "
        f"Stage: {_one_line(row.stage)} ({_one_line(row.stage_label)})",
        f"  Value: {_money_pair(row.value, row.currency)} | Base value: {_money_pair(row.base_value, row.base_currency)} | "
        f"Close: {row.expected_close_date.isoformat() if row.expected_close_date else '(none)'} | "
        f"Probability: {money_text(row.probability) if row.probability is not None else '(none)'}",
        f"  Selection verdict (top {top}): {actual.upper()}"
        + (f" (rank #{info['place']} of {info['eligible']})" if info and info.get("place") else ""),
    ]
    for state, text in steps:
        lines.append(f"    {state:<3}  {_one_line(text)}")
    if walked != actual:
        lines.append(f"  warning: the rule-by-rule walk says {walked.upper()}, but deals.py says {actual.upper()}; "
                     "deals.py is authoritative")
    if fixture:
        lines.append(f"  Test-data flag: looks like test data ({_one_line(fixture)}); ranked and flagged, never hidden")
    all_deal_issues = deal_issues + acct_issues
    if all_deal_issues:
        lines.append("  Data issues: " + "; ".join(_one_line(i) for i in all_deal_issues))

    lines.append("  Account facts:")
    if acct.get("found"):
        redacted = acct.get("redacted") or []
        name = "not available (redacted)" if "name" in redacted else _one_line(acct.get("name"))
        lines.append(f"    account {_one_line(acct.get('party_id'))}: {name}"
                     + (f" (redacted: {', '.join(_one_line(r) for r in redacted)})" if redacted else ""))
    else:
        lines.append(f"    account {_one_line(row.party_id)}: not found -> {accounts.ACCOUNT_NOT_FOUND}")
    lines.append(f"    activity source: {_one_line(acct.get('source'), 'none')}")
    lc = acct.get("last_contact")
    if lc:
        lines.append(f"    last contact: {_one_line(lc.get('date'))}, {_one_line(lc.get('type'))}, "
                     f"{_one_line(lc.get('subject'))}")
    else:
        lines.append(f"    last contact: none on or before {as_of.isoformat()}")
    acts = acct.get("open_activities") or []
    overdue = sum(1 for a in acts if a.get("overdue"))
    lines.append(f"    open activities: {len(acts)}" + (f" ({overdue} overdue)" if overdue else ""))
    for a in acts[:5]:
        due = _one_line(a.get("due_date"), "no due date")
        lines.append(f"      {_one_line(a.get('id'))}: due {due}{' (OVERDUE)' if a.get('overdue') else ''} — "
                     f"{_one_line(a.get('subject'))}")
    if len(acts) > 5:
        lines.append(f"      and {len(acts) - 5} more")
    if acct.get("other_open_deals") is not None:
        lines.append(f"    other open deals on account: {len(acct['other_open_deals'])}")
    if acct.get("pinned_note"):
        lines.append(f"    pinned note: {_one_line(acct['pinned_note'].get('title'))}")

    step = ns["step"]
    lines.append(f"  Next step (as of {as_of.isoformat()}): {_one_line(step['rule'])} — {_one_line(step['step'])}")
    lines.append(f"    reason: {_one_line(step['reason'])}")
    for r in ns["rules"]:
        lines.append(f"    [{_one_line(r['result'])}] {_one_line(r['rule'])}: {_one_line(r['why'])}")
    return lines


def run(client, wanted, as_of, top=report.DEFAULT_TOP, out=None):
    """Explain `wanted` on `out` (default stdout) and return the exit code."""
    out = out or sys.stdout
    as_of = business_date(as_of)
    labels = stage_labels(client.list_all("Pipeline"))
    rows = []
    for record in client.list_all("Deal"):
        try:
            rows.append(from_record(record, labels))
        except ValueError:
            continue
    print(f"Explaining deal {_one_line(wanted)}: brief date {as_of.isoformat()}, top {top}", file=out)
    row, matches, how = find_deal(rows, wanted)
    if row is None:
        if how == "ambiguous prefix":
            sample = ", ".join(_one_line(r.id) for r in matches[:5])
            more = f" (+{len(matches) - 5} more)" if len(matches) > 5 else ""
            print(f"  {_one_line(wanted)} matches {len(matches)} deals: {sample}{more}; give the full deal id",
                  file=out)
        else:
            print(f"  {_one_line(wanted)} is not the id of any deal this login can read", file=out)
        return EXIT_NOT_FOUND
    data = accounts.load(client, rows)
    acct = accounts.facts(client, row, as_of, data=data)
    print("", file=out)
    print("\n".join(describe_deal(row, how, rows, acct, as_of, top=top)), file=out)
    return 0


__all__ = ["EXIT_NOT_FOUND", "find_deal", "actual_verdict", "walk_selection", "describe_deal", "run"]
