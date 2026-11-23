"""Seat 30 suite, tasks 7-12 (Deepak). Tasks 1-6 are in tasks_deals.py (Sunil).

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

from . import changes
from .rows import from_dict, from_record, replace, stage_labels, to_dict
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
    try:
        from .report import build
    except ImportError:
        return None
    return build


def _run(client, ctx, folder, build=None):
    """One brief run against `folder`. Returns (baseline, {deal id: changes}, shown rows, mode)."""
    build = build if build is not None else _build()
    if build is not None:
        out = build(client, ctx["instance"], ctx["as_of"], snapshots=str(folder))
        shown = [from_dict(d["row"]) for d in out["deals"]]
        found = {d["row"]["id"]: list(d.get("changes") or []) for d in out["deals"]}
        return bool(out["baseline"]), found, shown, "full"
    # Component mode: a fixed, rank-free set of live deals stands in for the brief's list.
    rows = [r for r in _independent_rows(client) if r.stage_known and r.base_value is not None]
    shown = sorted(rows, key=lambda r: r.id)[:COMPONENT_TOP]
    result = changes.compare(shown, folder, ctx["instance"], ctx["as_of"])
    changes.save_snapshot(shown, ctx["instance"], ctx["as_of"], folder)   # as the brief does
    found = {r.id: result["per_deal"].get(r.id, []) for r in shown}
    return result["baseline"], found, shown, "component"


def _scratch(ctx, name):
    return tempfile.mkdtemp(prefix=f"{name}-", dir=ctx["workdir"])


def _week_before(ctx):
    return ctx["as_of"] - timedelta(days=7)


# ---------------------------------------------------------------- task 8

def what_changed(client, ctx, build=None):
    """A seeded stage change and a seeded value change are reported; unchanged deals are not.

    The platform cannot be edited (read-only, and no history), so the change is seeded in the
    previous week's snapshot instead: take the deals the brief shows, record one with a
    different stage and another with a different value, save that as last week, run again.
    """
    try:
        _, _, shown, mode = _run(client, ctx, _scratch(ctx, "t8-first"), build)
        stage_deal = next((r for r in shown if r.stage_known), None)
        value_deal = next((r for r in shown if r is not stage_deal and r.base_value is not None), None)
        if stage_deal is None or value_deal is None:
            return _result(False, f"infrastructure: {len(shown)} deals shown, need one with a known "
                                  "stage and another with a base value to seed", mode=mode)
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

        baseline, found, _, mode = _run(client, ctx, folder, build)
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

        others = [i for i, c in found.items() if c and i not in (stage_deal.id, value_deal.id)]
        if others:
            problems.append(f"{len(others)} unseeded deals reported as changed")

        evidence = (f"verifier=seeded_snapshot; deals={len(found)}; seeded=2; "
                    f"unchanged_reported={len(others)}")
        if problems:
            return _result(False, evidence + "; " + "; ".join(problems), mode=mode)
        return _result(True, evidence, mode=mode)
    except Exception as e:   # a task that cannot run is a failure marked infrastructure
        return _infrastructure(e)


# ---------------------------------------------------------------- task 9

def no_invented_change(client, ctx, build=None):
    """(a) With no earlier snapshot the run is a baseline and reports nothing as changed.
    (b) With last week's snapshot equal to this week's deals, nothing is reported as changed.

    For (b), a reported change only counts as invented if a fresh read still shows the old
    value; another login may really have edited a deal between our reads."""
    try:
        baseline, found, shown, mode = _run(client, ctx, _scratch(ctx, "t9-empty"), build)
        problems = []
        if not baseline:
            problems.append("first run with no snapshot was not marked baseline")
        claimed = sum(1 for c in found.values() if c)
        if claimed:
            problems.append(f"baseline run reported changes on {claimed} deals")

        folder = _scratch(ctx, "t9-same")
        changes.save_snapshot(shown, ctx["instance"], _week_before(ctx), folder)
        baseline_b, found_b, _, mode = _run(client, ctx, folder, build)
        if baseline_b:
            problems.append("run with last week's snapshot was marked baseline")

        # A reported change is genuine only if it matches what we saved as last week (before)
        # and a fresh read (after), and those two really differ. Anything else is invented.
        saved = {r.id: to_dict(r) for r in shown}
        truth = {r.id: to_dict(r) for r in _independent_rows(client)}
        invented, genuine = 0, 0
        for deal_id, deal_changes in found_b.items():
            for c in deal_changes:
                was = saved.get(deal_id, {}).get(c["field"], _MISSING)
                now = truth.get(deal_id, {}).get(c["field"], _MISSING)
                if was is not _MISSING and now is not _MISSING and was != now \
                        and c["before"] == was and c["after"] == now:
                    genuine += 1      # another login really edited the deal between our reads
                else:
                    invented += 1
        if invented:
            problems.append(f"{invented} changes reported that the data does not show")

        evidence = (f"verifier=identical_snapshot; deals={len(shown)}; baseline_changes={claimed}; "
                    f"invented={invented}; changed_by_other_logins={genuine}")
        if problems:
            return _result(False, evidence + "; " + "; ".join(problems), mode=mode)
        return _result(True, evidence, mode=mode)
    except Exception as e:
        return _infrastructure(e)


TASKS = [
    ("what_changed", "A seeded stage or value change is reported; an unchanged deal is not", what_changed),
    ("no_invented_change", "The first run is a baseline and no change is invented", no_invented_change),
]
