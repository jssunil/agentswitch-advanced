"""Seat 30 suite, tasks 1-6 (Sunil), in suite order. Tasks 7-12 are in tasks_brief.py (Deepak).

Each task is (task_id, title, fn), and fn(client, ctx) returns {passed, score, evidence}
(PLAN.md, "Interfaces", 8). ctx is a dict: instance, as_of (a date), workdir (a scratch folder).

Every task recounts the selection rules (PLAN.md, "Selection rules") from raw Deal and Pipeline
records with its own code in this file, never with deals.py, and judges the brief against that.
The recount shares only the agreed interface with deals.py: rows.from_record() to parse a deal
and rows.stage_usable() to judge its stage (PLAN.md, "Interfaces", 1).

Seeded deals. Some cases may not exist in the data on a given day (no unknown stage, no tie).
Tasks 2-6 then add a few made-up deals to what the brief reads, in memory only, through
SeededClient: nothing is written to the platform. Seeded ids start with "seat30-seed-" and are
built from a real open deal, so they have a real company, pipeline and base currency.

Modes. A task runs the whole brief (report.build) once it exists. Until then it runs deals.py
directly ("component" mode) and the score is capped at 0.5, as in tasks_brief.py.

Evidence holds counts and seeded ids only: no deal titles, accounts or values.
"""
import copy
import tempfile
from decimal import Decimal

from . import debug, optional_module
from .rows import from_dict, from_record, stage_labels, stage_usable
from .values import display_text

TOP = 10
SHOWN_IDS = 3
SEED = "seat30-seed-"
CLOSED_STAGES = frozenset({"closed_won", "closed_lost"})


# ---------------------------------------------------------------- the recount (independent of deals.py)

def recount(rows):
    """The selection rules, counted again from parsed rows:
    {closed, unknown_stage, value_unreadable, value_not_positive, currency, eligible} as id sets,
    and `groups` {(company_id, base_currency): [rows in rank order]}."""
    out = {k: set() for k in ("closed", "unknown_stage", "value_unreadable", "value_not_positive",
                              "currency", "eligible")}
    groups = {}
    for r in rows:
        usable = stage_usable(r)
        if usable and r.stage in CLOSED_STAGES:
            out["closed"].add(r.id)
        elif not usable:
            out["unknown_stage"].add(r.id)
        elif r.value is None:
            out["value_unreadable"].add(r.id)
        elif r.value <= 0:
            out["value_not_positive"].add(r.id)
        elif None in (r.currency, r.base_currency, r.base_value) or r.base_value <= 0:
            out["currency"].add(r.id)
        else:
            out["eligible"].add(r.id)
            groups.setdefault((r.company_id, r.base_currency), []).append(r)
    for g in groups.values():
        g.sort(key=lambda r: (-r.base_value, r.id))
    out["groups"] = groups
    return out


def expected_top(counted, n=TOP):
    return {k: [r.id for r in g[:n]] for k, g in counted["groups"].items()}


def _rows(client):
    labels = stage_labels(client.list_all("Pipeline"))
    return [from_record(r, labels) for r in client.list_all("Deal")]


# ---------------------------------------------------------------- seeding (in memory only)

class SeededClient:
    """Reads through `client`; Deal lists get `extra` records added (and, with `reverse`, come back
    in reverse order). Every other call goes to the real client unchanged, so writes stay blocked."""

    def __init__(self, client, extra=(), reverse=False):
        self._client, self._extra, self._reverse = client, [copy.deepcopy(e) for e in extra], reverse

    def list_all(self, entity, *args, **kwargs):
        records = self._client.list_all(entity, *args, **kwargs)
        if entity == "Deal":
            records = list(records) + copy.deepcopy(self._extra)
            if self._reverse:
                records.reverse()
        return records

    def __getattr__(self, name):
        return getattr(self._client, name)


def _template(client):
    """(raw record, parsed row, counted) for the largest eligible open deal: the seeds copy its
    company, pipeline, open stage and currencies. None when there is no eligible deal."""
    raw = client.list_all("Deal")
    labels = stage_labels(client.list_all("Pipeline"))
    rows = [from_record(r, labels) for r in raw]
    counted = recount(rows)
    eligible = [r for g in counted["groups"].values() for r in g]
    if not eligible:
        return None
    best = max(eligible, key=lambda r: (r.base_value, r.id))
    record = next(r for r in raw if str(r.get("id")) == best.id)
    return record, best, counted


def _seed(template, suffix, **over):
    record = copy.deepcopy(template)
    for key in ("party_id", "_party_id_display", "contact_id"):
        record[key] = None
    record.update({"id": SEED + suffix, "title": f"Seeded deal {suffix}"})
    record.update({k: (str(v) if isinstance(v, Decimal) else v) for k, v in over.items()})
    return record


# ---------------------------------------------------------------- running the brief

def _build():
    report = optional_module("report")
    return None if report is None else report.build


def _brief(client, ctx, build=None, n=TOP):
    """{mode, shown [DealRow], issues [{deal_id, issue}]} from the brief, or from deals.py before
    the brief exists. None when neither is built."""
    build = build if build is not None else _build()
    if build is not None:
        snapshots = tempfile.mkdtemp(prefix="t1-6-", dir=ctx["workdir"])
        out = build(client, ctx["instance"], ctx["as_of"], top=n, snapshots=snapshots)
        tied = {(g.get("company_id"), g.get("base_currency")): g.get("tied_beyond")
                for g in out.get("groups") or () if isinstance(g, dict) and g.get("tied_beyond")}
        return {"mode": "full", "shown": [from_dict(d["row"]) for d in out["deals"]],
                "issues": list(out.get("data_issues") or []), "tied": tied}
    deals = optional_module("deals")
    if deals is None:
        return None
    ranked, issues = deals.open_deals(_rows(client))
    return {"mode": "component", "shown": deals.top(ranked, n), "issues": issues,
            "tied": deals.tied_beyond(ranked, n)}


def _by_group(shown):
    out = {}
    for r in shown:
        out.setdefault((r.company_id, r.base_currency), []).append(r.id)
    return out


def _issue_ids(brief):
    return {str(i.get("deal_id")) for i in brief["issues"] if isinstance(i, dict)}


def _result(passed, evidence, mode, score=None):
    if score is None:
        score = 1.0 if passed else 0.0
    if mode == "component":
        score = min(score, 0.5)
        evidence = f"mode=component (brief.report not built yet); {evidence}"
    return {"passed": bool(passed), "score": score, "evidence": evidence}


def _infrastructure(reason):
    if isinstance(reason, Exception):
        debug.crash(reason)
        reason = f"{type(reason).__name__}: {display_text(reason)[:150]}"
    return {"passed": False, "score": 0.0, "evidence": f"infrastructure: {reason}"}


NOT_BUILT = "neither brief.report nor brief.deals is built yet"


def _listed_not_shown(brief, ids):
    """(missing from data issues, wrongly shown) for deals that must be left out and listed."""
    shown = {r.id for r in brief["shown"]}
    listed = _issue_ids(brief)
    return sorted(ids - listed), sorted(ids & shown)


def _diff_listed_not_shown(missing, shown):
    for i in missing[:SHOWN_IDS]:
        debug.diff(i, "not_listed", "listed in data_issues", "missing")
    for i in shown[:SHOWN_IDS]:
        debug.diff(i, "wrongly_shown", "left out of brief", "shown")


def _diff_ranking(want, got):
    shown = 0
    for k in sorted(set(want) | set(got), key=lambda x: (x[0] or "", x[1] or "")):
        w_ids, g_ids = want.get(k, []), got.get(k, [])
        for i in range(max(len(w_ids), len(g_ids))):
            w = w_ids[i] if i < len(w_ids) else None
            g = g_ids[i] if i < len(g_ids) else None
            if w != g:
                debug.diff(w or g, f"rank_{i + 1}({k[0]}:{k[1]})", w, g)
                shown += 1
                if shown >= SHOWN_IDS:
                    return


# ---------------------------------------------------------------- task 1

def top10_ids(client, ctx, build=None):
    """The deals shown, and their order, equal the recount's top 10 per company and base currency
    (all eligible deals when there are fewer). Partial credit: the share of positions that match."""
    try:
        brief = _brief(client, ctx, build)
        if brief is None:
            return _infrastructure(NOT_BUILT)
        want = expected_top(recount(_rows(client)))
        if not want:
            return _result(False, "infrastructure: no eligible open deals to rank", brief["mode"])
        got = _by_group(brief["shown"])
        positions = sum(len(v) for v in want.values())
        right = sum(1 for k, ids in want.items() for i, d in enumerate(ids)
                    if i < len(got.get(k, [])) and got[k][i] == d)
        extra_groups = len(set(got) - set(want))
        extra = sum(max(0, len(got.get(k, [])) - len(ids)) for k, ids in want.items()) \
            + sum(len(v) for k, v in got.items() if k not in want)
        evidence = (f"verifier=ranking_recount; groups={len(want)}; expected={positions}; "
                    f"shown={len(brief['shown'])}; matching_positions={right}; extra_shown={extra}; "
                    f"unexpected_groups={extra_groups}")
        passed = right == positions and not extra
        if not passed:
            _diff_ranking(want, got)
        # Extra deals count against the score, so a failure never gets full marks.
        return _result(passed, evidence, brief["mode"], score=round(right / (positions + extra), 2))
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 2

def open_only(client, ctx, build=None):
    """No closed won or closed lost deal is shown, and deals with no usable pipeline (none, or one
    that doesn't exist) still count as open (OBS-30-001). Seeded: a closed won and a closed lost
    deal, and an open deal with no pipeline and one naming a pipeline that doesn't exist, all
    larger than every real open deal, so the closed ones would be shown and the open ones must be."""
    try:
        t = _template(client)
        if t is None:
            return _infrastructure("no eligible open deal to build a seed from")
        record, best, counted = t
        big = dict(value=best.value * 3, _base_value=best.base_value * 3)
        open_stage = record.get("stage")
        seeds = [_seed(record, "closed-won", stage="closed_won", **big),
                 _seed(record, "closed-lost", stage="closed_lost", **big),
                 _seed(record, "no-pipeline", pipeline=None, stage=open_stage, **big),
                 _seed(record, "pipeline-not-found", pipeline="seat30_seed_no_such_pipeline", stage=open_stage, **big)]
        no_pipeline = {SEED + "no-pipeline", SEED + "pipeline-not-found"}
        seeded = SeededClient(client, seeds)
        brief = _brief(seeded, ctx, build)
        if brief is None:
            return _infrastructure(NOT_BUILT)
        counted = recount(_rows(seeded))
        closed = counted["closed"]
        shown = {r.id for r in brief["shown"]}
        bad = [i for i in shown if i in closed]
        missing = sorted(no_pipeline - shown)
        for i in bad[:SHOWN_IDS]:
            debug.diff(i, "closed_shown", "left out (closed)", "shown")
        for i in missing[:SHOWN_IDS]:
            debug.diff(i, "no_pipeline_missing", "shown (open)", "missing")
        real_no_pipeline = sum(1 for g in counted["groups"].values() for r in g
                               if not r.stage_known and not r.id.startswith(SEED))
        evidence = (f"verifier=stage_recount; closed_deals={len(closed)} (2 seeded); shown={len(shown)}; "
                    f"closed_shown={len(bad)}; no_pipeline_ranked={real_no_pipeline} real + 2 seeded; "
                    f"no_pipeline_seeds_missing={len(missing)}")
        if bad:
            evidence += f"; seeded_shown={sum(1 for i in bad if i.startswith(SEED))}"
        return _result(not bad and not missing, evidence, brief["mode"])
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 3

def unknown_stage(client, ctx, build=None):
    """Every deal with an unknown stage (real ones and a seeded one, larger than every open deal)
    is listed under data issues and never shown."""
    try:
        t = _template(client)
        if t is None:
            return _infrastructure("no eligible open deal to build a seed from")
        record, best, _ = t
        seeds = [_seed(record, "unknown-stage", stage="seat30_seed_no_such_stage",
                       value=best.value * 3, _base_value=best.base_value * 3)]
        seeded = SeededClient(client, seeds)
        brief = _brief(seeded, ctx, build)
        if brief is None:
            return _infrastructure(NOT_BUILT)
        unknown = recount(_rows(seeded))["unknown_stage"]
        missing, shown = _listed_not_shown(brief, unknown)
        _diff_listed_not_shown(missing, shown)
        evidence = (f"verifier=stage_recount; unknown_stage={len(unknown)} (1 seeded); "
                    f"not_listed={len(missing)}; shown={len(shown)}")
        score = round(1 - len(set(missing) | set(shown)) / len(unknown), 2)
        return _result(not missing and not shown, evidence, brief["mode"], score=score)
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 4

def currency_safe(client, ctx, build=None):
    """Ranking uses the base value: a seeded deal with a huge amount in its own currency but a
    small base value must not move up, and the ranking equals the recount. A deal with a positive
    value but no usable base value or currency (real ones, and a seeded "ZZZ" deal) is listed
    under data issues and never shown. Each list holds one company and base currency."""
    try:
        t = _template(client)
        if t is None:
            return _infrastructure("no eligible open deal to build a seed from")
        record, best, counted = t
        smallest = min(r.base_value for r in counted["groups"][(best.company_id, best.base_currency)])
        seeds = [_seed(record, "no-base-value", currency="ZZZ", value=best.value * 3, _base_value=None),
                 _seed(record, "foreign-amount", value=best.value * 100, _base_value=smallest)]
        seeded = SeededClient(client, seeds)
        brief = _brief(seeded, ctx, build)
        if brief is None:
            return _infrastructure(NOT_BUILT)
        c = recount(_rows(seeded))
        missing, shown = _listed_not_shown(brief, c["currency"])
        _diff_listed_not_shown(missing, shown)
        want, got = expected_top(c), _by_group(brief["shown"])
        problems = []
        if missing or shown:
            problems.append(f"currency issues not listed={len(missing)}, shown={len(shown)}")
        if got != want:
            _diff_ranking(want, got)
            problems.append("ranking differs from the base-value recount")
        no_base_cur = [r for r in brief["shown"] if r.base_currency is None]
        for r in no_base_cur[:SHOWN_IDS]:
            debug.diff(r.id, "base_currency", "three-letter code", None)
        mixed = len(no_base_cur)
        if mixed:
            problems.append(f"{mixed} shown deals without a base currency")
        evidence = (f"verifier=base_value_recount; currency_issues={len(c['currency'])} (1 seeded); "
                    f"groups={len(want)}; shown={len(brief['shown'])}")
        return _result(not problems, "; ".join([evidence] + problems), brief["mode"])
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 5

def zero_value(client, ctx, build=None):
    """Deals whose own value is zero or less (or can't be read) are left out and listed under data
    issues. Seeded: a zero-value deal and a negative one, each with a large base value, so a brief
    that ranks on base_value alone would show them."""
    try:
        t = _template(client)
        if t is None:
            return _infrastructure("no eligible open deal to build a seed from")
        record, best, _ = t
        seeds = [_seed(record, "zero-value", value="0", _base_value=best.base_value * 3),
                 _seed(record, "negative-value", value="-1", _base_value=best.base_value * 3)]
        seeded = SeededClient(client, seeds)
        brief = _brief(seeded, ctx, build)
        if brief is None:
            return _infrastructure(NOT_BUILT)
        c = recount(_rows(seeded))
        left_out = c["value_not_positive"] | c["value_unreadable"]
        missing, shown = _listed_not_shown(brief, left_out)
        _diff_listed_not_shown(missing, shown)
        evidence = (f"verifier=value_recount; value_zero_or_less={len(c['value_not_positive'])} (2 seeded); "
                    f"value_unreadable={len(c['value_unreadable'])}; not_listed={len(missing)}; shown={len(shown)}")
        score = round(1 - len(set(missing) | set(shown)) / len(left_out), 2)
        return _result(not missing and not shown, evidence, brief["mode"], score=score)
    except Exception as e:
        return _infrastructure(e)


# ---------------------------------------------------------------- task 6

def tie_break(client, ctx, build=None):
    """Two seeded deals with the same base value, larger than every open deal, ids "...tie-10" and
    "...tie-9": the order is by deal id as text ("10" before "9"), and two runs, the second reading
    the deals in reverse order, show the same list."""
    try:
        t = _template(client)
        if t is None:
            return _infrastructure("no eligible open deal to build a seed from")
        record, best, _ = t
        tied = dict(value=best.value * 3, _base_value=best.base_value * 3)
        seeds = [_seed(record, "tie-9", **tied), _seed(record, "tie-10", **tied)]
        # A tie at the cut-off too (Deepak's review of 1a3199d, point 3): two deals with the value of
        # the deal that becomes 10th once the two above are added, so 3 or more deals share it there.
        key = (best.company_id, best.base_currency)
        group = recount(_rows(client))["groups"].get(key, [])
        if len(group) >= TOP - 2:
            at_cut = group[TOP - 3].base_value
            seeds += [_seed(record, "cut-a", value=at_cut, _base_value=at_cut),
                      _seed(record, "cut-b", value=at_cut, _base_value=at_cut)]
        first = _brief(SeededClient(client, seeds), ctx, build)
        if first is None:
            return _infrastructure(NOT_BUILT)
        second = _brief(SeededClient(client, seeds, reverse=True), ctx, build)
        got = _by_group(first["shown"]).get(key, [])
        problems = []
        if got[:2] != [SEED + "tie-10", SEED + "tie-9"]:
            debug.diff(SEED + "tie-10", "top_2", [SEED + "tie-10", SEED + "tie-9"], got[:2])
            problems.append("seeded tie not in deal-id order (as text)")
        if [r.id for r in first["shown"]] != [r.id for r in second["shown"]]:
            debug.diff(key[0] or "deals", "reverse_read",
                       [r.id for r in first["shown"]], [r.id for r in second["shown"]])
            problems.append("a second run, reading deals in reverse order, showed a different list")
        want_cut = {k: sum(1 for r in g[TOP:] if r.base_value == g[TOP - 1].base_value)
                    for k, g in recount(_rows(SeededClient(client, seeds)))["groups"].items() if len(g) > TOP}
        want_cut = {k: v for k, v in want_cut.items() if v}
        if first.get("tied") != want_cut:
            debug.diff(f"{key[0]}:{key[1]}", "tied_beyond", want_cut, first.get("tied"))
            problems.append("deals sharing the value at the cut-off not reported as the recount finds them")
        real_ties = sum(1 for g in recount(_rows(client))["groups"].values()
                        for a, b in zip(g, g[1:]) if a.base_value == b.base_value)
        evidence = (f"verifier=seeded_tie; seeded={len(seeds)}; runs=2; real_adjacent_ties={real_ties}; "
                    f"tied_at_cut_off={sum(want_cut.values())}")
        return _result(not problems, "; ".join([evidence] + problems), first["mode"])
    except Exception as e:
        return _infrastructure(e)


TASKS = [
    ("top10_ids", "The 10 largest open deals, in order, match a recount", top10_ids),
    ("open_only", "No closed won or closed lost deal is shown", open_only),
    ("unknown_stage", "Deals with an unknown stage are data issues, never ranked", unknown_stage),
    ("currency_safe", "Ranking uses the base value; unknown currencies are flagged", currency_safe),
    ("zero_value", "Deals with value of zero or less are left out and counted", zero_value),
    ("tie_break", "Tied values come out in deal-id order on every run", tie_break),
]
