"""What changed: the brief's own week-to-week history.

The platform keeps no history for deals, so the brief records its own. Each run saves the
deals it showed as a snapshot (`<folder>/<instance>-<YYYY-MM-DD>.json`), and the next run
compares with the latest snapshot dated *before* its own date. A rerun on the same date
replaces that day's snapshot and still compares with the earlier one.

With no earlier snapshot the run is a baseline, and nothing is reported as changed. The
module never infers a change from timestamps or guesses one: a change is a field whose
saved value differs from today's value.

Snapshots hold customer data (deal titles, accounts). They belong in the gitignored
`briefs/` folder and are written with owner-only permissions.
"""
import json
import os
import re
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

from .rows import from_dict, to_dict
from .values import business_date

FORMAT = 1

# Fields a reader cares about. Left out on purpose: updated_at/updated_by (they move with any
# edit and say nothing on their own), rot_days and rot_level (they grow every day by themselves),
# issues and fixture_flag (the brief's own labels, not the deal's).
TRACKED = ("title", "account", "owner", "pipeline", "stage", "stage_label", "value", "currency",
           "base_value", "base_currency", "probability", "expected_close_date")

CLOSED = {"closed_won": "closed won", "closed_lost": "closed lost"}

_INSTANCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_NAME = re.compile(r"(?P<instance>[a-z0-9][a-z0-9_-]{0,63})-(?P<day>\d{4}-\d{2}-\d{2})\.json\Z")


class SnapshotError(Exception):
    """A saved snapshot cannot be read. The brief stops rather than compare against a guess."""


def save_snapshot(rows, instance, as_of, folder):
    """Save `rows` (the deals the brief shows, in rank order) and return the file's path."""
    instance, day = _instance(instance), _day(as_of)
    rows = list(rows)
    _unique(rows)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{instance}-{day.isoformat()}.json"
    if target.is_symlink():
        raise SnapshotError(f"{target.name}: a snapshot must not be a symlink")
    payload = {"format": FORMAT, "instance": instance, "date": day.isoformat(),
               "rows": [to_dict(r) for r in rows]}
    fd, temporary = tempfile.mkstemp(prefix=".snapshot-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:   # mkstemp creates the file with mode 0600
            json.dump(payload, f, ensure_ascii=False, indent=1)
        os.replace(temporary, target)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    return target


def compare(rows, folder, instance, as_of, all_rows=None):
    """Compare this run's deals with the latest earlier snapshot.

    `rows` are the deals the brief shows now. `all_rows` (optional) are every deal read this
    run. With them, a deal that left the list gets a reason: closed won or lost, stage not
    recognised, value changed, or outranked. Without them the reason is only "not in this
    week's list".

    Returns {baseline, previous_date, per_deal {deal id: [{field, before, after}]},
    entered [deal ids], dropped [{id, title, reason}]}. per_deal only has deals with changes.
    """
    instance, day = _instance(instance), _day(as_of)
    rows = list(rows)
    _unique(rows)
    previous = latest_before(folder, instance, day)
    if previous is None:
        return {"baseline": True, "previous_date": None, "per_deal": {}, "entered": [], "dropped": []}
    previous_date, before_rows = previous
    before = {r.id: r for r in before_rows}
    now = {r.id: r for r in rows}

    per_deal = {}
    for row in rows:
        old = before.get(row.id)
        if old is not None:
            diffs = field_changes(old, row)
            if diffs:
                per_deal[row.id] = diffs
    entered = [r.id for r in rows if r.id not in before]
    everything = {r.id: r for r in all_rows} if all_rows is not None else None
    dropped = [{"id": old.id, "title": old.title, "reason": _drop_reason(old, everything)}
               for old in before_rows if old.id not in now]
    return {"baseline": False, "previous_date": previous_date.isoformat(), "per_deal": per_deal,
            "entered": entered, "dropped": dropped}


def field_changes(old, new):
    """[{field, before, after}] for every tracked field whose value differs, in TRACKED order.
    Money is compared as a number, so 1250000.5 and 1250000.50 are the same value."""
    changes = []
    before, after = to_dict(old), to_dict(new)
    for name in TRACKED:
        if not _same(getattr(old, name), getattr(new, name)):
            changes.append({"field": name, "before": before[name], "after": after[name]})
    return changes


def latest_before(folder, instance, as_of):
    """(date, rows) of the latest snapshot for `instance` dated before `as_of`, or None."""
    instance, day = _instance(instance), _day(as_of)
    folder = Path(folder)
    if not folder.is_dir():
        return None
    dated = []
    for path in folder.iterdir():
        m = _NAME.fullmatch(path.name)
        if not m or m["instance"] != instance:
            continue
        try:
            snap_day = date.fromisoformat(m["day"])
        except ValueError:
            continue
        if snap_day < day:
            dated.append((snap_day, path))
    if not dated:
        return None
    snap_day, path = max(dated)
    return snap_day, _load(path, instance, snap_day)


def _load(path, instance, snap_day):
    """Read one snapshot. Anything unexpected is a SnapshotError: never fall back to an older
    file, because comparing against the wrong week would report changes that did not happen."""
    if path.is_symlink():
        raise SnapshotError(f"{path.name}: a snapshot must not be a symlink")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("format") != FORMAT:
            raise ValueError("unknown snapshot format")
        if payload.get("instance") != instance or payload.get("date") != snap_day.isoformat():
            raise ValueError("instance or date does not match the file name")
        if not isinstance(payload.get("rows"), list):
            raise ValueError("rows must be a list")
        rows = [from_dict(r) for r in payload["rows"]]
        _unique(rows)
        return rows
    except (OSError, ValueError, TypeError, AttributeError) as e:
        raise SnapshotError(f"{path.name}: cannot read the snapshot ({e})") from None


def _drop_reason(old, everything):
    if everything is None:
        return "not in this week's list"
    now = everything.get(old.id)
    if now is None:
        return "no longer in the CRM (deleted, or not visible to this seat)"
    if now.stage in CLOSED:
        return CLOSED[now.stage]
    if not now.stage_known:
        return "stage not recognised"
    if not _same(old.base_value, now.base_value):
        return "value changed"
    return "outranked by other deals"


def _same(a, b):
    if isinstance(a, Decimal) and isinstance(b, Decimal):
        return a == b
    return a == b and type(a) is type(b)


def _unique(rows):
    seen = set()
    for r in rows:
        if r.id in seen:
            raise ValueError("the same deal appears twice")
        seen.add(r.id)


def _instance(value):
    if not isinstance(value, str) or not _INSTANCE.fullmatch(value):
        raise ValueError("instance must be lower-case letters, digits, '_' or '-'")
    return value


def _day(value):
    day = business_date(value)
    if day is None:
        raise ValueError("a date is needed")
    return day
