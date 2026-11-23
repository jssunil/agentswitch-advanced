"""changes.py: snapshots, comparison, baseline, and refusing to compare against a bad file."""
import json
import os
import stat
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from brief import changes
from brief.rows import from_record, replace, stage_labels

from tests.fakes import PIPELINES, deal

LABELS = stage_labels(PIPELINES)
TODAY = date(2026, 11, 30)
LAST_WEEK = date(2026, 11, 23)


def rows(*records):
    return [from_record(r, LABELS) for r in records]


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


class Baseline(Base):
    def test_no_snapshot_is_baseline_with_nothing_changed(self):
        out = changes.compare(rows(deal(1), deal(2)), self.folder, "keystone", TODAY)
        self.assertEqual(out, {"baseline": True, "previous_date": None, "per_deal": {},
                               "entered": [], "dropped": []})

    def test_missing_folder_is_baseline(self):
        out = changes.compare(rows(deal(1)), self.folder / "nope", "keystone", TODAY)
        self.assertTrue(out["baseline"])

    def test_same_day_and_later_snapshots_are_ignored(self):
        changes.save_snapshot(rows(deal(1, stage="new")), "keystone", TODAY, self.folder)
        changes.save_snapshot(rows(deal(1, stage="new")), "keystone", date(2026, 12, 7), self.folder)
        self.assertTrue(changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)["baseline"])

    def test_other_instance_is_ignored(self):
        changes.save_snapshot(rows(deal(1, stage="new")), "suryodaya", LAST_WEEK, self.folder)
        self.assertTrue(changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)["baseline"])


class Compare(Base):
    def test_identical_week_reports_nothing(self):
        current = rows(deal(1), deal(2), deal(3))
        changes.save_snapshot(current, "keystone", LAST_WEEK, self.folder)
        out = changes.compare(current, self.folder, "keystone", TODAY)
        self.assertFalse(out["baseline"])
        self.assertEqual(out["previous_date"], "2026-11-23")
        self.assertEqual((out["per_deal"], out["entered"], out["dropped"]), ({}, [], []))

    def test_stage_value_and_date_changes(self):
        changes.save_snapshot(rows(deal(1, stage="qualification"), deal(2, _base_value=500.0), deal(3)),
                              "keystone", LAST_WEEK, self.folder)
        now = rows(deal(1), deal(2), deal(3, expected_close_date="2027-01-10"))
        out = changes.compare(now, self.folder, "keystone", TODAY)
        self.assertEqual(out["per_deal"]["d01"][0], {"field": "stage", "before": "qualification", "after": "proposal"})
        self.assertEqual(out["per_deal"]["d01"][1]["field"], "stage_label")
        self.assertEqual(out["per_deal"]["d02"], [{"field": "base_value", "before": "500.0", "after": "2000.5"}])
        self.assertEqual(out["per_deal"]["d03"], [{"field": "expected_close_date",
                                                   "before": "2026-12-15", "after": "2027-01-10"}])

    def test_noise_fields_are_not_changes(self):
        changes.save_snapshot(rows(deal(1)), "keystone", LAST_WEEK, self.folder)
        now = rows(deal(1, _rot_days=99, _rot_level="critical", updated_at="2026-11-29T10:00:00Z", updated_by="u9"))
        self.assertEqual(changes.compare(now, self.folder, "keystone", TODAY)["per_deal"], {})

    def test_money_compared_as_numbers(self):
        changes.save_snapshot(rows(deal(1, value="1000.50")), "keystone", LAST_WEEK, self.folder)
        self.assertEqual(changes.compare(rows(deal(1, value=1000.5)), self.folder, "keystone", TODAY)["per_deal"], {})

    def test_unknown_to_known_is_a_change(self):
        changes.save_snapshot(rows(deal(1, value=None)), "keystone", LAST_WEEK, self.folder)
        out = changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)
        self.assertEqual(out["per_deal"]["d01"], [{"field": "value", "before": None, "after": "1000.5"}])

    def test_latest_earlier_snapshot_wins(self):
        changes.save_snapshot(rows(deal(1, stage="new")), "keystone", date(2026, 11, 16), self.folder)
        changes.save_snapshot(rows(deal(1, stage="negotiation")), "keystone", LAST_WEEK, self.folder)
        out = changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)
        self.assertEqual(out["per_deal"]["d01"][0]["before"], "negotiation")


class EnteredAndDropped(Base):
    def setUp(self):
        super().setUp()
        changes.save_snapshot(rows(deal(1), deal(2), deal(3), deal(4), deal(5)), "keystone", LAST_WEEK, self.folder)

    def test_without_all_rows_the_reason_is_plain(self):
        out = changes.compare(rows(deal(1), deal(6)), self.folder, "keystone", TODAY)
        self.assertEqual(out["entered"], ["d06"])
        self.assertEqual({d["reason"] for d in out["dropped"]}, {"not in this week's list"})
        self.assertEqual([d["id"] for d in out["dropped"]], ["d02", "d03", "d04", "d05"])

    def test_reasons_from_all_rows(self):
        everything = rows(deal(1), deal(2, stage="closed_won"), deal(3, stage="bogus"),
                          deal(4, _base_value=10.0), deal(6))   # d05 is gone
        shown = [r for r in everything if r.id in ("d01", "d06")]
        reasons = {d["id"]: d["reason"] for d in
                   changes.compare(shown, self.folder, "keystone", TODAY, all_rows=everything)["dropped"]}
        self.assertEqual(reasons, {"d02": "closed won", "d03": "stage not recognised", "d04": "value changed",
                                   "d05": "no longer in the CRM (deleted, or not visible to this seat)"})

    def test_outranked(self):
        everything = rows(deal(1), deal(2), deal(3), deal(4), deal(5), deal(9))
        shown = [r for r in everything if r.id != "d05"]
        out = changes.compare(shown, self.folder, "keystone", TODAY, all_rows=everything)
        self.assertEqual(out["dropped"][0]["reason"], "outranked by other deals")


class Files(Base):
    def test_snapshot_round_trip_is_exact_and_private(self):
        r = replace(rows(deal(1, _base_value="12345678901234567890.07"))[0], fixture_flag=True)
        path = changes.save_snapshot([r], "keystone", LAST_WEEK, self.folder)
        self.assertEqual(path.name, "keystone-2026-11-23.json")
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        day, loaded = changes.latest_before(self.folder, "keystone", TODAY)
        self.assertEqual((day, loaded), (LAST_WEEK, [r]))
        self.assertEqual(loaded[0].base_value, Decimal("12345678901234567890.07"))

    def test_rerun_same_day_replaces_the_file(self):
        changes.save_snapshot(rows(deal(1)), "keystone", TODAY, self.folder)
        changes.save_snapshot(rows(deal(2)), "keystone", TODAY, self.folder)
        self.assertEqual([p.name for p in self.folder.iterdir()], ["keystone-2026-11-30.json"])

    def test_bad_snapshot_stops_the_run(self):
        good = rows(deal(1, stage="new"))
        changes.save_snapshot(good, "keystone", date(2026, 11, 16), self.folder)
        bad = self.folder / "keystone-2026-11-23.json"
        for content in ("{not json", json.dumps({"format": 2, "rows": []}),
                        json.dumps({"format": 1, "instance": "suryodaya", "date": "2026-11-23", "rows": []}),
                        json.dumps({"format": 1, "instance": "keystone", "date": "2026-11-23", "rows": [{}]})):
            bad.write_text(content)
            with self.assertRaises(changes.SnapshotError, msg=content):
                changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)

    def test_symlinked_snapshot_is_refused(self):
        target = self.folder / "elsewhere.json"
        target.write_text("{}")
        (self.folder / "keystone-2026-11-23.json").symlink_to(target)
        with self.assertRaises(changes.SnapshotError):
            changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)
        with self.assertRaises(changes.SnapshotError):
            changes.save_snapshot(rows(deal(1)), "keystone", LAST_WEEK, self.folder)

    def test_bad_inputs(self):
        for instance in ("../x", "Keystone", "", None):
            with self.assertRaises(ValueError):
                changes.save_snapshot(rows(deal(1)), instance, TODAY, self.folder)
        with self.assertRaises(ValueError):
            changes.save_snapshot(rows(deal(1), deal(1)), "keystone", TODAY, self.folder)
        with self.assertRaises(ValueError):
            changes.compare(rows(deal(1)), self.folder, "keystone", None)
        self.assertEqual(list(self.folder.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
