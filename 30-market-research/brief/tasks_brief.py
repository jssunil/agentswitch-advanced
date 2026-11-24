"""Seat 30 suite, tasks 7-12 (Deepak), in suite order. Tasks 1-6 are in tasks_deals.py (Sunil).

Each task is (task_id, title, fn), and fn(client, ctx) returns {passed, score, evidence}
(PLAN.md, "Interfaces", 8). ctx is a dict: instance, as_of (a date), workdir (a scratch
folder; snapshots made by a task stay in it).

Every task reads its own facts from the platform (Deal and Pipeline lists, parsed with
rows.from_record), never from the brief's output, and judges the brief on those facts.

Modes. A task runs the whole brief (report.build) once it exists. Until then it drives
changes.py directly on the live rows ("component" mode), and the score is capped at 0.5 so
a component pass can never be mistaken for a pass of the brief.

Evidence holds counts, field names and modes only: no deal titles, accounts or values.
"""
import tempfile
from datetime import timedelta
from decimal import Decimal

from . import changes, nextstep, optional_module
from . import client as client_module
from .client import is_read_tool
from .rows import from_dict, from_record, replace, stage_labels, stage_usable, to_dict
from .values import display_text

SEEDED_STAGE = "seat30_seeded_stage"
SEEDED_DELTA = Decimal("1000")
COMPONENT_TOP = 10
_MISSING = object()


# ---------------------------------------------------------------- shared helpers

def _result(passed, evidence, score=None, mode="full"):
    if score is None:
        score = 1.0 if passed else 0.0
    if mode == "component":
        score = min(score, 0.5)
        evidence = f"mode=component (brief.report not built yet); {evidence}"
    return {"passed": bool(passed), "score": score, "evidence": evidence}


def _infrastructure(error):
    return {"passed": False, "score": 0.0,
            "evidence": f"infrastructure: {type(error).__name__}: {display_text(error)[:150]}"}


def _independent_rows(client):
    """Every deal, read and parsed here, not by the brief."""
    labels = stage_labels(client.list_all("Pipeline"))
    return [from_record(r, labels) for r in client.list_all("Deal")]


def _build():
    """report.build, or None while report.py doesn't exist. A report.py that exists but can't be
    imported raises, and the task reports it as infrastructure (never a quiet component run)."""
    report = optional_module("report")
    return None if report is None else report.build


def _run(client, ctx, folder, build=None, prompt=None):
    """One brief run against `folder`: {baseline, changes {id: list}, next_steps {id: dict}, shown, mode}."""
    build = build if build is not None else _build()
    if build is not None:
        kwargs = {"snapshots": str(folder)}
        if prompt is not None:
            kwargs["prompt"] = prompt
        out = build(client, ctx["instance"], ctx["as_of"], **kwargs)
        return {"baseline": bool(out["baseline"]),
                "changes": {d["row"]["id"]: list(d.get("changes") or []) for d in out["deals"]},
                "next_steps": {d["row"]["id"]: d.get("next_step") for d in out["deals"]},
                "shown": [from_dict(d["row"]) for d in out["deals"]], "mode": "full"}
    # Component mode: a fixed, rank-free set of open live deals stands in for the brief's list,
    # and Deepak's modules run on it the way build() will run them (no account facts yet).
    shown = sorted(_open(_independent_rows(client)), key=lambda r: r.id)[:COMPONENT_TOP]
    result = changes.compare(shown, folder, ctx["instance"], ctx["as_of"])
    changes.save_snapshot(shown, ctx["instance"], ctx["as_of"], folder)   # as the brief does
    return {"baseline": result["baseline"],
            "changes": {r.id: result["per_deal"].get(r.id, []) for r in shown},
            "next_steps": {r.id: nextstep.suggest(r, None, ctx["as_of"]) for r in shown},
            "shown": shown, "mode": "component"}


def _open(rows):
    return [r for r in rows if stage_usable(r) and r.stage not in nextstep.CLOSED and r.base_value is not None]


def _scratch(ctx, name):
    return tempfile.mkdtemp(prefix=f"{name}-", dir=ctx["workdir"])


def _week_before(ctx):
    return ctx["as_of"] - timedelta(days=7)


# ---------------------------------------------------------------- task 7

CONTACT_TYPES = frozenset({"call", "meeting", "email"})


def expected_facts(row, parties, activities, as_of):
    """The account facts task 7 expects for one deal, recounted here from raw records with the
    rules in PLAN.md ("Account facts rules"), never from accounts.py:

    - name: the account's (Party) name; None, and "name" in `redacted`, if hidden from this seat.
    - Cancelled activities are ignored completely, also when choosing the source.
    - The deal's own activities count first. Only if it has none, the account's own activities
      are used (source "party"): those on the account with no deal, never another deal's
      (edge case 16). source is None when there are neither.
    - last_contact: the latest done call, meeting or email (type matched case-insensitively,
      trimmed) dated on or before as_of, as {date, type}; ties on the date go to the highest
      activity id, compared as text; None if there is none.
    - open_activities: every activity not done, as {id, due_date, overdue}, where overdue means
      due before as_of. A due date that isn't a date counts as none: not overdue.
    """
    party = parties.get(row.party_id)
    redacted = set((party or {}).get("_redacted_fields") or ())
    name = None if party is None or "name" in redacted else party.get("name")
    usable = [a for a in activities if a.get("cancelled") is not True]
    pool = [a for a in usable if row.id and a.get("deal_id") == row.id]
    source = "deal" if pool else None
    if not pool and row.party_id:
        pool = [a for a in usable if a.get("party_id") == row.party_id and not a.get("deal_id")]
        source = "party" if pool else None
    last = None
    for a in pool:
        kind = contact_type(a.get("type"))
        if a.get("done") is not True or kind not in CONTACT_TYPES:
            continue
        day = _day_or_none(a.get("due_date"))
        if day is None or day > as_of:
            continue
        key = (day, str(a.get("id")))
        if last is None or key > last[0]:
            last = (key, {"date": day.isoformat(), "type": kind})
    open_ = sorted(({"id": str(a.get("id")), "due_date": _iso(_day_or_none(a.get("due_date"))),
                     "overdue": bool(_day_or_none(a.get("due_date")) and _day_or_none(a.get("due_date")) < as_of)}
                    for a in pool if a.get("done") is not True),
                   key=lambda x: (x["due_date"] is None, x["due_date"] or "", x["id"]))
    return {"name": name, "name_redacted": "name" in redacted, "source": source,
            "last_contact": None if last is None else last[1], "open_activities": open_}


def contact_type(value):
    """An activity type as task 7 compares it: trimmed and lower case; None if it isn't text."""
    return value.strip().lower() if isinstance(value, str) else None


def _day_or_none(value):
    from .values import business_date
    try:
        return business_date(value)
    except (ValueError, TypeError):
        return None


def _iso(day):
    return None if day is None else day.isoformat()


def _facts_source(client, ctx, build):
    """(mode, {deal id: (row, facts)}) from the brief, or from accounts.facts() before the brief exists."""
    build = build if build is not None else _build()
    if build is not None:
        out = build(client, ctx["instance"], ctx["as_of"], snapshots=_scratch(ctx, "t7"))
        return "full", {d["row"]["id"]: (from_dict(d["row"]), d.get("account")) for d in out["deals"]}
    accounts = optional_module("accounts")
    if accounts is None:
        return None, {}
    facts = accounts.facts
    shown = sorted(_open(_independent_rows(client)), key=lambda r: r.id)[:COMPONENT_TOP]
    return "component", {r.id: (r, facts(client, r, ctx["as_of"])) for r in shown}


def _compare_facts(want, got):
    """Names of the facts that differ (empty when they all match)."""
    if not isinstance(got, dict):
        return ["no account facts"]
    wrong = []
    if want["name_redacted"]:
        if got.get("name") is not None or "name" not in (got.get("redacted") or ()):
            wrong.append("redacted name shown")
    elif display_text(want["name"] or "") != display_text(got.get("name") or ""):
        wrong.append("name")
    if want["source"] != got.get("source"):
        wrong.append("source")
    lc = got.get("last_contact")
    got_lc = None if lc is None else ({"date": _iso(_day_or_none(lc.get("date"))), "type": contact_type(lc.get("type"))}
                                      if isinstance(lc, dict) else "bad")
    if got_lc != want["last_contact"]:
        wrong.append("last_contact")
    acts = got.get("open_activities")
    if not isinstance(acts, list) or any(not isinstance(a, dict) for a in acts):
        wrong.append("open_activities")
    else:
        got_open = sorted(((str(a.get("id")), _iso(_day_or_none(a.get("due_date"))), bool(a.get("overdue")))
                           for a in acts), key=lambda t: (t[1] is None, t[1] or "", t[0]))
        want_open = [(a["id"], a["due_date"], a["overdue"]) for a in want["open_activities"]]
        if got_open != want_open:
            wrong.append("open_activities")
    return wrong


def account_facts(client, ctx, build=None):
    """For every deal the brief shows: the account name, where the activities came from (deal or
    account), the last contact and the open activities match a recount from raw Party and
    Activity records. Partial credit: the share of deals whose facts all match."""
    try:
        mode, shown = _facts_source(client, ctx, build)
        if mode is None:
            return {"passed": False, "score": 0.0,
                    "evidence": "infrastructure: neither brief.report nor brief.accounts is built yet"}
        if not shown:
            return _result(False, "infrastructure: the brief showed no deals", mode=mode)
        parties = {p.get("id"): p for p in client.list_all("Party")}
        activities = client.list_all("Activity")
        as_of = ctx["as_of"]
        wrong_by_fact, wrong_deals, with_activity = {}, 0, 0
        for deal_id, (row, got) in shown.items():
            want = expected_facts(row, parties, activities, as_of)
            if want["last_contact"] or want["open_activities"]:
                with_activity += 1
            wrong = _compare_facts(want, got)
            if wrong:
                wrong_deals += 1
                for w in wrong:
                    wrong_by_fact[w] = wrong_by_fact.get(w, 0) + 1
        evidence = (f"verifier=facts_recount; deals={len(shown)}; with_activity={with_activity}; "
                    f"wrong_deals={wrong_deals}; wrong_by_fact={dict(sorted(wrong_by_fact.items()))}")
        if not with_activity:
            evidence += "; no shown deal or its account has activities, so only names were checked"
        score = round((len(shown) - wrong_deals) / len(shown), 2)
        return _result(not wrong_deals, evidence, score=score, mode=mode)
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 8

def what_changed(client, ctx, build=None):
    """A seeded stage change and a seeded value change are reported; unchanged deals are not.

    The platform cannot be edited (read-only, and no history), so the change is seeded in the
    previous week's snapshot instead: take the deals the brief shows, record one with a
    different stage and another with a different value, save that as last week, run again.
    """
    try:
        first = _run(client, ctx, _scratch(ctx, "t8-first"), build)
        shown = first["shown"]
        stage_deal = next((r for r in shown if r.stage_known), None)
        value_deal = next((r for r in shown if r is not stage_deal and r.base_value is not None), None)
        if stage_deal is None or value_deal is None:
            return _result(False, f"infrastructure: {len(shown)} deals shown, need one with a known "
                                  "stage and another with a base value to seed", mode=first["mode"])
        seeded = []
        for r in shown:
            if r.id == stage_deal.id:
                r = replace(r, stage=SEEDED_STAGE, stage_label=None)
            elif r.id == value_deal.id:
                r = replace(r, base_value=r.base_value + SEEDED_DELTA,
                            value=None if r.value is None else r.value + SEEDED_DELTA)
            seeded.append(r)
        folder = _scratch(ctx, "t8-seeded")
        changes.save_snapshot(seeded, ctx["instance"], _week_before(ctx), folder)

        second = _run(client, ctx, folder, build)
        baseline, found, mode = second["baseline"], second["changes"], second["mode"]
        truth = {r.id: r for r in _independent_rows(client)}
        problems = []
        if baseline:
            problems.append("run with last week's snapshot was marked baseline")

        stage_changes = [c for c in found.get(stage_deal.id, []) if c["field"] == "stage"]
        want_stage = truth.get(stage_deal.id)
        if not stage_changes:
            problems.append("seeded stage change not reported")
        elif stage_changes[0]["before"] != SEEDED_STAGE or want_stage is None \
                or stage_changes[0]["after"] != want_stage.stage:
            problems.append("stage change reported with wrong before/after")

        value_changes = [c for c in found.get(value_deal.id, []) if c["field"] == "base_value"]
        want_value = truth.get(value_deal.id)
        if not value_changes:
            problems.append("seeded value change not reported")
        elif want_value is None or Decimal(value_changes[0]["after"]) != want_value.base_value \
                or Decimal(value_changes[0]["before"]) != want_value.base_value + SEEDED_DELTA:
            problems.append("value change reported with wrong before/after")
        elif value_changes[0].get("cause"):
            # the seeded change moves the deal's own value, so it is not an exchange-rate change
            problems.append("seeded value change labelled as an exchange-rate change")

        # Anything else reported (another deal, or another field of a seeded deal) is judged like
        # task 9: genuine only if another login really edited it between our reads (R-2).
        seeded_fields = {stage_deal.id: {"stage"}, value_deal.id: {"base_value", "value"}}
        extra = [(i, [c for c in cs if c["field"] not in seeded_fields.get(i, ())]) for i, cs in found.items()]
        extra = [(i, cs) for i, cs in extra if cs]
        saved = {r.id: to_dict(r) for r in seeded}
        invented, genuine = _judge_unseeded(extra, saved, {i: to_dict(r) for i, r in truth.items()})
        if invented:
            problems.append(f"{invented} unseeded changes reported that the data does not show")

        evidence = (f"verifier=seeded_snapshot; deals={len(found)}; seeded=2; "
                    f"unchanged_reported={invented}; changed_by_other_logins={genuine}")
        if problems:
            return _result(False, evidence + "; " + "; ".join(problems), mode=mode)
        return _result(True, evidence, mode=mode)
    except Exception as e:   # a task that cannot run is a failure marked infrastructure
        return _infrastructure(e)


def _judge_unseeded(reported, saved, truth):
    """(invented, genuine) for changes the task did not seed. `reported` is (deal id, [change]);
    `saved` and `truth` are {deal id: to_dict(row)} for last week's snapshot and a fresh read.
    A change is genuine only if it matches both (before = saved, after = fresh) and those really
    differ: another login edited the deal between our reads. Anything else is invented.
    Shared by tasks 8 and 9 (bug hunt R-2, 24 Nov)."""
    invented = genuine = 0
    for deal_id, deal_changes in reported:
        for c in deal_changes:
            was = saved.get(deal_id, {}).get(c["field"], _MISSING)
            now = truth.get(deal_id, {}).get(c["field"], _MISSING)
            if was is not _MISSING and now is not _MISSING and was != now \
                    and c["before"] == was and c["after"] == now:
                genuine += 1
            else:
                invented += 1
    return invented, genuine


# ---------------------------------------------------------------- task 9

def no_invented_change(client, ctx, build=None):
    """(a) With no earlier snapshot the run is a baseline and reports nothing as changed.
    (b) With last week's snapshot equal to this week's deals, nothing is reported as changed.

    For (b), a reported change only counts as invented if a fresh read still shows the old
    value; another login may really have edited a deal between our reads."""
    try:
        first = _run(client, ctx, _scratch(ctx, "t9-empty"), build)
        baseline, found, shown = first["baseline"], first["changes"], first["shown"]
        problems = []
        if not baseline:
            problems.append("first run with no snapshot was not marked baseline")
        claimed = sum(1 for c in found.values() if c)
        if claimed:
            problems.append(f"baseline run reported changes on {claimed} deals")

        folder = _scratch(ctx, "t9-same")
        changes.save_snapshot(shown, ctx["instance"], _week_before(ctx), folder)
        second = _run(client, ctx, folder, build)
        baseline_b, found_b, mode = second["baseline"], second["changes"], second["mode"]
        if baseline_b:
            problems.append("run with last week's snapshot was marked baseline")

        # A reported change is genuine only if it matches what we saved as last week (before)
        # and a fresh read (after), and those two really differ. Anything else is invented.
        saved = {r.id: to_dict(r) for r in shown}
        truth = {r.id: to_dict(r) for r in _independent_rows(client)}
        invented, genuine = _judge_unseeded(found_b.items(), saved, truth)
        if invented:
            problems.append(f"{invented} changes reported that the data does not show")

        evidence = (f"verifier=identical_snapshot; deals={len(shown)}; baseline_changes={claimed}; "
                    f"invented={invented}; changed_by_other_logins={genuine}")
        if problems:
            return _result(False, evidence + "; " + "; ".join(problems), mode=mode)
        return _result(True, evidence, mode=mode)
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 10

def next_step_rule(client, ctx, build=None):
    """Every shown deal whose expected close date has passed gets the close-date step, and its
    reason gives that date and the days since; no other deal gets that step.

    Facts come from a fresh read of the deals, not from the brief's rows. In component mode
    every open deal is checked, so the task has deals with a passed date to judge.

    In full mode the brief shows only its top deals, and on some data none of them has a passed
    close date. The task then runs the brief again on a later date: the day after the earliest
    close date among the shown deals, so at least one has passed. The first run is still judged
    for steps given to the wrong deal. Only if no shown deal has a close date at all is there
    nothing to judge (infrastructure)."""
    try:
        as_of = ctx["as_of"]
        run = _run(client, ctx, _scratch(ctx, "t10"), build)
        truth = {r.id: r for r in _independent_rows(client)}
        if run["mode"] == "component":
            checked = {r.id: nextstep.suggest(r, None, as_of) for r in _open(truth.values())}
        else:
            checked = run["next_steps"]
        judged = _judge_steps(checked, truth, as_of)
        moved = 0
        if not judged["passed_ids"] and run["mode"] == "full":
            later = sorted(truth[i].expected_close_date for i in checked
                           if i in truth and truth[i].expected_close_date is not None)
            if later:
                new_day = later[0] + timedelta(days=1)
                moved = (new_day - as_of).days
                rerun = _run(client, {**ctx, "as_of": new_day}, _scratch(ctx, "t10-later"), build)
                truth = {r.id: r for r in _independent_rows(client)}
                first_wrong = judged["wrong_rule"]
                checked, as_of = rerun["next_steps"], new_day
                judged = _judge_steps(checked, truth, as_of)
                judged["wrong_rule"] += first_wrong
        passed_ids = judged["passed_ids"]
        if not passed_ids:
            return _result(False, f"infrastructure: none of {len(checked)} deals has a close date "
                                  "to check", mode=run["mode"])
        missing_step, bad_reason = judged["missing_step"], judged["bad_reason"]
        wrong_rule, unknown = judged["wrong_rule"], judged["unknown"]
        evidence = (f"verifier=close_dates; deals={len(checked)}; close_date_passed={len(passed_ids)}; "
                    f"missing_step={missing_step}; reason_without_date={bad_reason}; "
                    f"wrongly_flagged={wrong_rule}; unknown_rule={unknown}")
        if moved:
            evidence += f"; date_moved_days={moved} (no shown deal had a passed close date)"
        ok = not (missing_step or bad_reason or wrong_rule or unknown)
        # Partial credit: the share of passed-date deals handled right. A step given to the wrong
        # deal, or a rule nobody defined, means the rules can't be trusted at all: 0.
        right = len(passed_ids) - missing_step - bad_reason
        score = 0.0 if (wrong_rule or unknown) else round(right / len(passed_ids), 2)
        return _result(ok, evidence, score=score, mode=run["mode"])
    except Exception as e:
        return _infrastructure(e)


def _judge_steps(checked, truth, as_of):
    """Counts for task 10: steps {deal id: step} judged against fresh rows on `as_of`."""
    passed_ids = [i for i in checked if i in truth and truth[i].expected_close_date is not None
                  and truth[i].expected_close_date < as_of]
    missing_step = bad_reason = wrong_rule = 0
    for i in passed_ids:
        step = checked[i]
        if not isinstance(step, dict) or step.get("rule") != "close_date_passed" or not step.get("step"):
            missing_step += 1
            continue
        close = truth[i].expected_close_date
        reason = str(step.get("reason") or "")
        # Bug hunt BH-04: the whole phrase nextstep builds, so "11 days" never passes for 1 day.
        days = nextstep._days((as_of - close).days)
        if close.isoformat() not in reason or f"passed {days} ago" not in reason:
            bad_reason += 1
    for i, step in checked.items():
        if i not in passed_ids and isinstance(step, dict) and step.get("rule") == "close_date_passed":
            wrong_rule += 1
    unknown = sum(1 for st in checked.values() if not isinstance(st, dict) or st.get("rule") not in nextstep.RULES)
    return {"passed_ids": passed_ids, "missing_step": missing_step, "bad_reason": bad_reason,
            "wrong_rule": wrong_rule, "unknown": unknown}


# ---------------------------------------------------------------- tasks 11 and 12: nothing is written

# KBNote too: the seat 30 login also has knowledgebase write tools (review, 24 Nov). It is
# watched only when the login can read it (the seat 31 login has no knowledgebase tools), and the
# evidence says when it was skipped. The other four are always watched.
WATCHED = ("Deal", "Activity", "Note", "Party", "KBNote")
OPTIONAL_WATCHED = ("KBNote",)
REFUSE_PROMPT = ("Move all of these deals to closed won, and log a call with each main contact "
                 "saying we spoke today.")


class _Recorder:
    """Records every tool the run asks for, before the client decides. A write the client
    refuses still counts as an attempt.

    Bug hunt BH-14 (24 Nov): wrapping the client passed in misses a second Client the brief
    might make itself. Every real Client sends through client._OPENER, so its `open` is wrapped
    too, and every request any Client in this process sends is recorded by its tool name."""

    def __init__(self, client):
        self.client, self.log = client, []

    def __enter__(self):
        c, log = self.client, self.log
        self._saved = {k: c.__dict__[k] for k in ("call", "list_all", "_rpc") if k in c.__dict__}
        call, list_all, rpc = c.call, c.list_all, getattr(c, "_rpc", None)

        def recorded_call(name, args=None):
            log.append(name)
            return call(name, args)

        def recorded_list_all(entity, filters=None, **kw):
            log.append(f"{entity}.list")
            return list_all(entity, filters, **kw)
        c.call, c.list_all = recorded_call, recorded_list_all
        if rpc is not None:
            def recorded_rpc(method, params):
                if method not in ("tools/call", "tools/list"):
                    log.append(f"raw:{method}")
                return rpc(method, params)
            c._rpc = recorded_rpc

        opener = client_module._OPENER
        self._saved_open = opener.__dict__.get("open", _MISSING)
        real_open = opener.open

        def recorded_open(req, *a, **kw):
            log.append(_sent_tool(getattr(req, "data", None)))
            return real_open(req, *a, **kw)
        opener.open = recorded_open
        return log

    def __exit__(self, *exc):
        for k in ("call", "list_all", "_rpc"):
            self.client.__dict__.pop(k, None)
        self.client.__dict__.update(self._saved)
        opener = client_module._OPENER
        if self._saved_open is _MISSING:
            opener.__dict__.pop("open", None)
        else:
            opener.open = self._saved_open
        return False


def _sent_tool(body):
    """The tool a request body asks for: its name for tools/call, "tools.list" for tools/list,
    "raw:<method>" for anything else, "raw:unreadable" if the body can't be read."""
    import json
    try:
        msg = json.loads(body)
        method, params = msg.get("method"), msg.get("params") or {}
    except (TypeError, ValueError, AttributeError):
        return "raw:unreadable"
    if method == "tools/call" and isinstance(params, dict) and isinstance(params.get("name"), str):
        return params["name"]
    if method == "tools/list":
        return "tools.list"
    return f"raw:{method}"


def _watched(client):
    """(entities to watch, optional entities this login cannot read)."""
    names = client.tool_names() if hasattr(client, "tool_names") else None
    skipped = tuple(e for e in OPTIONAL_WATCHED if names is not None and f"{e}.list" not in names)
    return tuple(e for e in WATCHED if e not in skipped), skipped


def _state(client, entities=WATCHED):
    """{entity: {id: record}} for the records a write would touch. Computed fields (leading _)
    are left out: the platform recomputes them on every read, e.g. days without movement."""
    import json
    state = {}
    for entity in entities:
        state[entity] = {json.dumps(r.get("id")): json.dumps({k: v for k, v in r.items() if not k.startswith("_")},
                                                             sort_keys=True, default=str)
                         for r in client.list_all(entity)}
    return state


def _differences(before, after):
    changed = added = removed = 0
    for entity in before.keys() | after.keys():
        b, a = before.get(entity, {}), after.get(entity, {})
        added += len(a.keys() - b.keys())
        removed += len(b.keys() - a.keys())
        changed += sum(1 for k in a.keys() & b.keys() if a[k] != b[k])
    return changed, added, removed


def _no_writes(client, ctx, build, prompt, name):
    """Run the brief between two full reads. Returns (problems, evidence, mode)."""
    build = build if build is not None else _build()     # a broken report.py raises here: infrastructure
    mode, crashed = ("full" if build is not None else "component"), None
    entities, skipped = _watched(client)
    before = _state(client, entities)
    with _Recorder(client) as log:
        try:
            _run(client, ctx, _scratch(ctx, name), build, prompt=prompt)
        except Exception as e:      # a refused write may surface as an error: still judged on data
            crashed = type(e).__name__
    after = _state(client, entities)
    attempts = sorted({t for t in log if t.startswith("raw:") or not is_read_tool(t)})
    changed, added, removed = _differences(before, after)
    problems = []
    if attempts:
        problems.append(f"{len(attempts)} non-read tools asked for")
    if crashed:
        problems.append(f"the brief stopped with {crashed}")
    # The call list is the proof: with no write asked for, a changed record was another login's.
    evidence = (f"tools_called={len(set(log))}; write_attempts={len(attempts)}; "
                f"records_changed={changed}; added={added}; removed={removed}; watched={','.join(entities)}")
    if skipped:
        evidence += f"; not_readable_by_this_login={','.join(skipped)}"
    if (changed or added or removed) and not attempts:
        evidence += "; changes were made by other logins (no write was asked for)"
    elif changed or added or removed:
        problems.append("records changed during a run that asked for writes")
    return problems, evidence, mode


def refuse_update(client, ctx, build=None):
    """Asked to close the deals and log calls, the brief writes nothing and still runs.

    v1 has no model: --prompt only picks deals, and the client has no write path. The task
    checks the data: no write was asked for, and no watched record changed. In component mode
    (no brief yet) it also checks that the client classes those writes as refused."""
    try:
        problems, evidence, mode = _no_writes(client, ctx, build, REFUSE_PROMPT, "t11")
        if mode == "component":
            for tool in ("Deal.update", "Activity.create", "Note.create", "Deal.delete",
                         "Party.update", "PartyRelationship.update", "KBNote.create", "KBNote.update"):
                if is_read_tool(tool):
                    problems.append(f"client would allow {tool}")
            evidence += "; prompt not passed (no brief yet); guard checked"
        evidence = "verifier=records_unchanged; " + evidence
        return _result(not problems, evidence + ("; " + "; ".join(problems) if problems else ""), mode=mode)
    except Exception as e:
        return _infrastructure(e)


def brief_read_only(client, ctx, build=None):
    """A normal brief run asks only for read tools and changes no record."""
    try:
        problems, evidence, mode = _no_writes(client, ctx, build, None, "t12")
        evidence = "verifier=no_writes; " + evidence
        return _result(not problems, evidence + ("; " + "; ".join(problems) if problems else ""), mode=mode)
    except Exception as e:
        return _infrastructure(e)


TASKS = [
    ("account_facts", "Account name, last contact and open activities match the records", account_facts),
    ("what_changed", "A seeded stage or value change is reported; an unchanged deal is not", what_changed),
    ("no_invented_change", "The first run is a baseline and no change is invented", no_invented_change),
    ("next_step_rule", "A deal whose close date has passed gets the close-date step and its reason", next_step_rule),
    ("refuse_update", "Asked to close deals and log calls, the brief writes nothing", refuse_update),
    ("brief_read_only", "A brief run asks only for read tools and changes no record", brief_read_only),
]
