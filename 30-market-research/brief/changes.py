"""What changed: the brief's own week-to-week history.

The platform keeps no history for deals, so the brief records its own. Each run saves the
deals it showed as a snapshot (`<folder>/<instance>-<YYYY-MM-DD>.json`), and the next run
compares with the latest snapshot dated *before* its own date. A rerun on the same date
replaces that day's snapshot and still compares with the earlier one.

With no earlier snapshot the run is a baseline, and nothing is reported as changed. The
module never infers a change from timestamps or guesses one: a change is a field whose
saved value differs from today's value.

A snapshot holds every ranked open deal, not only the ones shown, plus the ids that were shown
(edge case 7). So a different `--top` next week gives no false "entered" or "dropped", and a
deal that enters the list still gets its changes. With `top`, last week's list is cut per company
and base currency, as `deals.top()` cuts this week's. A change in the base value alone, with the
deal's own value and currency unchanged, is marked as an exchange-rate change (edge case 6).

Folders (edge case 8): the weekly run uses the official `briefs/snapshots/`; a run with
`--date` or `--prompt` uses `briefs/test-snapshots/` unless a folder is given, so a test run
never becomes the comparison point for a real one (`snapshot_folder`). The official folder
also refuses a snapshot dated after today (`save_snapshot(..., not_after=today)`).

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

from .rows import from_dict, stage_usable, to_dict
from .values import business_date

FORMAT = 1

# Fields a reader cares about. Left out on purpose: updated_at/updated_by (they move with any
# edit and say nothing on their own), rot_days and rot_level (they grow every day by themselves),
# issues and fixture_flag (the brief's own labels, not the deal's), and stage_label: a renamed
# label in the pipeline is not a change to the deal, and a change of stage shows in `stage`
# (edge case 22).
TRACKED = ("title", "account", "owner", "pipeline", "stage", "value", "currency",
           "base_value", "base_currency", "probability", "expected_close_date")

EXCHANGE_RATE = "exchange rate"
OFFICIAL_FOLDER = Path("briefs") / "snapshots"
TEST_FOLDER = Path("briefs") / "test-snapshots"

CLOSED = {"closed_won": "closed won", "closed_lost": "closed lost"}

_INSTANCE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z")
_NAME = re.compile(r"(?P<instance>[a-z0-9][a-z0-9_-]{0,63})-(?P<day>\d{4}-\d{2}-\d{2})\.json\Z")


class SnapshotError(Exception):
    """A saved snapshot cannot be read. The brief stops rather than compare against a guess."""


class NoSnapshot(LookupError):
    """`--since` names a date with no snapshot. The brief stops (exit 2) and never picks
    another week instead."""


def snapshot_folder(snapshots=None, date_given=False, prompt=None):
    """The folder a run reads and writes snapshots in: the one given, else the test folder for
    a run with --date or --prompt, else the official weekly folder."""
    if snapshots:
        return Path(snapshots)
    return TEST_FOLDER if date_given or prompt else OFFICIAL_FOLDER


def save_snapshot(rows, instance, as_of, folder, ranked=None, not_after=None):
    """Save this run and return the file's path.

    `rows` are the deals the brief shows, in rank order. `ranked` (optional) is every ranked open
    deal, in rank order; it is what the file holds, so next week can recompute its own top N.
    A shown deal missing from `ranked` is added after it. `not_after` (a date, optional) refuses
    a snapshot dated later; the command line passes today for the official folder."""
    instance, day = _instance(instance), _day(as_of)
    rows = list(rows)
    _unique(rows)
    if not_after is not None and day > _day(not_after):
        raise SnapshotError(f"refusing a snapshot dated {day.isoformat()}, after "
                            f"{_day(not_after).isoformat()}, in this folder")
    stored = list(ranked) if ranked is not None else list(rows)
    _unique(stored)
    known = {r.id for r in stored}
    stored += [r for r in rows if r.id not in known]
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{instance}-{day.isoformat()}.json"
    if target.is_symlink():
        raise SnapshotError(f"{target.name}: a snapshot must not be a symlink")
    payload = {"format": FORMAT, "instance": instance, "date": day.isoformat(),
               "rows": [to_dict(r) for r in stored], "shown": [r.id for r in rows]}
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


def compare(rows, folder, instance, as_of, all_rows=None, top=None, since=None):
    """Compare this run's deals with an earlier snapshot.

    `rows` are the deals the brief shows now. `all_rows` (optional) are every deal read this
    run. With them, a deal that left the list gets a reason: closed won or lost, stage not
    recognised, value changed, an exchange-rate change, or outranked. Without them the reason is
    only "not in this week's list".

    `top` is this run's --top. With it, last week's list is recomputed from the saved ranking as
    the first `top` deals of each company and base currency, as deals.top() shows them (one
    list per company, instructor answer 5), so a different --top gives no false entered or
    dropped deals. Without it, last week's list is the deals it showed. `since` (a date before `as_of`) compares with the snapshot of exactly that
    day, and raises NoSnapshot if there is none; without it, the latest snapshot before `as_of`.

    Returns {baseline, previous_date, per_deal {deal id: [{field, before, after, cause?}]},
    entered [deal ids], dropped [{id, title, reason}]}. per_deal only has deals with changes.
    A change in the base value alone carries cause "exchange rate".
    """
    instance, day = _instance(instance), _day(as_of)
    rows = list(rows)
    _unique(rows)
    if top is not None and (isinstance(top, bool) or not isinstance(top, int) or top < 1):
        raise ValueError("top must be a positive whole number")
    previous = _snapshot_on(folder, instance, day, _day(since)) if since is not None \
        else _latest(folder, instance, day)
    if previous is None:
        return {"baseline": True, "previous_date": None, "per_deal": {}, "entered": [], "dropped": []}
    previous_date, before_rows, shown_ids, unsaved = previous
    before = {r.id: r for r in before_rows}
    now = {r.id: r for r in rows}
    if shown_ids is None:                 # a snapshot holding only the shown deals
        last_list = before_rows
    elif top is None:                     # last week's list as it was shown
        shown_set = set(shown_ids)
        last_list = [r for r in before_rows if r.id in shown_set]
    else:                                 # last week's ranking, cut to this run's --top per group
        last_list = first_per_group(before_rows, top)

    per_deal = {}
    for row in rows:
        old = before.get(row.id)
        if old is not None:
            diffs = field_changes(old, row, skip=unsaved)
            if diffs:
                per_deal[row.id] = diffs
    last_ids = {r.id for r in last_list}
    entered = [r.id for r in rows if r.id not in last_ids]
    everything = {r.id: r for r in all_rows} if all_rows is not None else None
    dropped = [{"id": old.id, "title": old.title, "reason": _drop_reason(old, everything)}
               for old in last_list if old.id not in now]
    return {"baseline": False, "previous_date": previous_date.isoformat(), "per_deal": per_deal,
            "entered": entered, "dropped": dropped}


def first_per_group(ranked, n):
    """The first n deals of each (company, base currency) group, in the order given. The same
    cut as deals.top(), so last week's list is compared like for like (Sunil's review, point 2)."""
    seen = {}
    out = []
    for r in ranked:
        key = (r.company_id, r.base_currency)
        if seen.get(key, 0) < n:
            seen[key] = seen.get(key, 0) + 1
            out.append(r)
    return out


def field_changes(old, new, skip=()):
    """[{field, before, after}] for every tracked field whose value differs, in TRACKED order.
    Money is compared as a number, so 1250000.5 and 1250000.50 are the same value. Fields in
    `skip` (tracked fields an older snapshot never saved) are not compared: their saved value
    is unknown, not empty (edge case 5)."""
    changes = []
    before, after = to_dict(old), to_dict(new)
    for name in TRACKED:
        if name in skip:
            continue
        if not _same(getattr(old, name), getattr(new, name)):
            change = {"field": name, "before": before[name], "after": after[name]}
            if name == "base_value" and _rate_only(old, new):
                change["cause"] = EXCHANGE_RATE
            changes.append(change)
    return changes


def _rate_only(old, new):
    """The base value moved while the deal's own value and currency (and the base currency)
    stayed the same: only the exchange rate changed."""
    return (old.value is not None and _same(old.value, new.value) and old.currency == new.currency
            and old.base_currency == new.base_currency and not _same(old.base_value, new.base_value))


def latest_before(folder, instance, as_of):
    """(date, rows) of the latest snapshot for `instance` dated before `as_of`, or None."""
    found = _latest(folder, instance, as_of)
    return None if found is None else found[:2]


def _snapshot_on(folder, instance, as_of, since):
    """(date, rows, shown ids or None, unsaved tracked fields) of the snapshot dated exactly `since`."""
    instance = _instance(instance)
    if since >= as_of:
        raise ValueError("--since must be a date before the brief's date")
    path = Path(folder) / f"{instance}-{since.isoformat()}.json"
    if not path.exists() and not path.is_symlink():
        raise NoSnapshot(f"no {instance} snapshot dated {since.isoformat()} in {folder}")
    return (since, *_load(path, instance, since))


def _latest(folder, instance, as_of):
    """(date, rows, shown ids or None, unsaved tracked fields) of the latest snapshot dated before
    `as_of`, or None."""
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
    return (snap_day, *_load(path, instance, snap_day))


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
        # A DealRow field added after this file was written loads as None (edge case 5). If that
        # field is tracked, its old value is unknown, so compare() must not report it as changed.
        unsaved = frozenset(name for name in TRACKED
                            if any(isinstance(r, dict) and name not in r for r in payload["rows"]))
        shown = payload.get("shown")
        if shown is not None:
            if not isinstance(shown, list) or not all(isinstance(i, str) for i in shown) \
                    or len(set(shown)) != len(shown) or not set(shown) <= {r.id for r in rows}:
                raise ValueError("shown must list ids of saved deals, once each")
        return rows, shown, unsaved
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
    if not stage_usable(now):             # a deal with no usable pipeline is open and ranked (OBS-30-001)
        return "stage not recognised"
    if not _same(old.base_value, now.base_value):
        if _rate_only(old, now):
            return "outranked after an exchange-rate change"
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
