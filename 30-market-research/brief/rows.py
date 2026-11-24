"""The deal row: the one format both halves of seat 30 share.

Sunil's half (deals.py, accounts.py, report.py) and Deepak's half (changes.py, nextstep.py,
the suite) meet only here. The fields are frozen from 24 Nov 2026: changing, renaming or
removing one needs agreement from both (see PLAN.md, "Interfaces").

from_record() never guesses. A value that is missing stays None; a value that is present
but malformed also becomes None, and the reason goes into `issues`, so the brief can list
it as a data issue instead of dropping the deal silently. Rules about which deals are open,
how they rank and which look like test fixtures belong to deals.py, not here.
"""
from dataclasses import dataclass, fields, replace
from datetime import date
from decimal import Decimal

from .values import business_date, currency_code, display_text, money


@dataclass(frozen=True)
class DealRow:
    id: str
    title: str
    company_id: str | None
    party_id: str | None
    account: str | None             # the account's display name, as the platform gives it
    contact_id: str | None
    owner: str | None
    pipeline: str | None            # pipeline key; None for deals with no pipeline
    stage: str | None               # stage key
    stage_label: str | None         # label from the deal's pipeline; None if the stage is not in it
    stage_known: bool               # True only when the stage key is a stage of the deal's pipeline
    value: Decimal | None           # in `currency`
    currency: str | None            # three-letter code as recorded; may still be a code nobody knows
    base_value: Decimal | None      # platform-computed value in `base_currency`
    base_currency: str | None
    probability: Decimal | None
    expected_close_date: date | None
    rot_days: int | None            # platform-computed days without movement
    rot_level: str | None
    updated_at: str | None          # as recorded (ISO timestamp), compared as text
    updated_by: str | None
    issues: tuple[str, ...] = ()    # "field: reason" for every value that could not be read
    fixture_flag: bool = False      # set by deals.py with dataclasses.replace(); never by from_record()


FIELDS = tuple(f.name for f in fields(DealRow))


def stage_labels(pipelines):
    """{(company_id, pipeline key): {stage key: label}} from Pipeline records.

    A deal names its pipeline by key, and keys are only unique within a company.
    Malformed pipelines or stages are skipped; their deals then show stage_known=False.
    """
    labels = {}
    for p in pipelines or ():
        if not isinstance(p, dict) or not isinstance(p.get("key"), str):
            continue
        stages = {}
        for s in p.get("stages") or ():
            if isinstance(s, dict) and isinstance(s.get("key"), str):
                label = s.get("label") or s.get("name") or s["key"]
                stages[s["key"]] = display_text(label)
        labels[(_text(p.get("company_id")), p["key"])] = stages
    return labels


PIPELINE_MISSING = "pipeline: missing, so the stage has no label"
PIPELINE_NOT_FOUND = "pipeline: not found, so the stage has no label"
STAGE_NOT_IN_PIPELINE = "stage: not a stage of the deal's pipeline"


def _stages_for(labels, company_id, pipeline):
    """The deal's own company first; otherwise a pipeline key that only one company uses."""
    if pipeline is None:
        return None
    if (company_id, pipeline) in labels:
        return labels[(company_id, pipeline)]
    matches = [v for (_, key), v in labels.items() if key == pipeline]
    return matches[0] if len(matches) == 1 else None


def _pipeline_exists(labels, pipeline):
    """True if any company has a pipeline with this key (even one we can't pick between)."""
    return any(key == pipeline for (_, key) in labels)


def stage_usable(row):
    """True when the deal's stage can be used to judge it open or closed: a stage of its own
    pipeline, or, for a deal with no usable pipeline (none, or one that doesn't exist), its stage
    key. False for a missing stage and for a stage that isn't in a pipeline that does exist
    (the unknown-stage data issue, task 3). Shared by deals.py, the tasks and the snapshot run."""
    if row.stage is None:
        return False
    return row.stage_known or PIPELINE_MISSING in row.issues or PIPELINE_NOT_FOUND in row.issues


def from_record(record, labels=None):
    """A DealRow from one deal record. Raises ValueError only for a record with no id."""
    if not isinstance(record, dict) or record.get("id") in (None, ""):
        raise ValueError("a deal record needs an id")
    issues = []

    def read(name, parse, key=None):
        raw = record.get(key or name)
        try:
            return parse(raw)
        except (ValueError, TypeError):
            issues.append(f"{name}: not readable")
            return None

    company_id = _text(record.get("company_id"))
    pipeline = _text(record.get("pipeline"))
    stage = _text(record.get("stage"))
    labels = labels or {}
    stages = _stages_for(labels, company_id, pipeline)
    stage_known = stages is not None and stage in stages
    if stage is None:
        issues.append("stage: missing")
    elif pipeline is None:
        issues.append(PIPELINE_MISSING)
    elif stages is None and not _pipeline_exists(labels, pipeline):
        # The deal names a pipeline no company has (seen on Suryodaya: "default"). Like a deal
        # with no pipeline, it is judged by its stage key; see stage_usable() and OBS-30-001.
        issues.append(PIPELINE_NOT_FOUND)
    elif not stage_known:
        issues.append(STAGE_NOT_IN_PIPELINE)

    currency = _currency(record.get("currency"), issues, "currency")
    base_currency = _currency(record.get("_base_currency"), issues, "base_currency")

    return DealRow(
        id=str(record["id"]),
        title=display_text(record.get("title") or ""),
        company_id=company_id,
        party_id=_text(record.get("party_id")),
        account=_display(record.get("_party_id_display")),
        contact_id=_text(record.get("contact_id")),
        owner=_display(record.get("owner")),
        pipeline=pipeline,
        stage=stage,
        stage_label=stages.get(stage) if stage_known else None,
        stage_known=stage_known,
        value=read("value", money),
        currency=currency,
        base_value=read("base_value", money, "_base_value"),
        base_currency=base_currency,
        probability=read("probability", money),
        expected_close_date=read("expected_close_date", business_date),
        rot_days=read("rot_days", _whole, "_rot_days"),
        rot_level=_text(record.get("_rot_level")),
        updated_at=read("updated_at", _timestamp),
        updated_by=_text(record.get("updated_by")),
        issues=tuple(issues),
    )


def to_dict(row):
    """JSON-safe dict: money as exact decimal text, dates as YYYY-MM-DD, issues as a list."""
    out = {}
    for name in FIELDS:
        v = getattr(row, name)
        if isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, date):
            v = v.isoformat()
        elif isinstance(v, tuple):
            v = list(v)
        out[name] = v
    return out


_SAVED_TEXT = ("company_id", "party_id", "account", "contact_id", "owner", "pipeline", "stage",
               "stage_label", "currency", "base_currency", "rot_level", "updated_at", "updated_by")


def from_dict(data):
    """The inverse of to_dict(), for saved snapshots. Unknown keys are ignored.
    Raises ValueError for a row without an id or title, so a damaged snapshot is never read."""
    if not isinstance(data, dict) or not isinstance(data.get("id"), str) or not data["id"] \
            or not isinstance(data.get("title"), str):
        raise ValueError("a saved deal row needs an id and a title")
    values = {name: data.get(name) for name in FIELDS}
    # Bug hunt BH-09: every other field has the type to_dict() saves, or is missing (None, for a
    # field added later). Anything else is a damaged snapshot.
    for name in _SAVED_TEXT:
        if values[name] is not None and not isinstance(values[name], str):
            raise ValueError(f"a saved deal row has a {name} that is not text")
    if values["issues"] is not None and (not isinstance(values["issues"], list)
                                         or not all(isinstance(i, str) for i in values["issues"])):
        raise ValueError("a saved deal row has issues that are not a list of text")
    for name in ("stage_known", "fixture_flag"):
        if values[name] is not None and not isinstance(values[name], bool):
            raise ValueError(f"a saved deal row has a {name} that is not true or false")
    if values["rot_days"] is not None and (isinstance(values["rot_days"], bool)
                                           or not isinstance(values["rot_days"], int)):
        raise ValueError("a saved deal row has a rot_days that is not a whole number")
    for name in ("value", "base_value", "probability"):
        values[name] = money(values[name])
    values["expected_close_date"] = business_date(values["expected_close_date"])
    values["issues"] = tuple(values["issues"] or ())
    values["stage_known"] = bool(values["stage_known"])
    values["fixture_flag"] = bool(values["fixture_flag"])
    return DealRow(**values)


__all__ = ["DealRow", "FIELDS", "stage_labels", "from_record", "to_dict", "from_dict", "replace",
           "stage_usable", "PIPELINE_MISSING", "PIPELINE_NOT_FOUND", "STAGE_NOT_IN_PIPELINE"]


def _text(v):
    if v is None or v == "":
        return None
    return v if isinstance(v, str) else str(v)


def _display(v):
    return None if v in (None, "") else display_text(v)


def _currency(raw, issues, name):
    if raw in (None, ""):
        return None
    code = currency_code(raw)
    if code is None:
        issues.append(f"{name}: not a three-letter code")
    return code


def _whole(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError("expected a whole number")
    return v


def _timestamp(v):
    if v in (None, ""):
        return None
    if not isinstance(v, str):
        raise ValueError("expected a timestamp")
    business_date(v)   # validates the whole value
    return v
