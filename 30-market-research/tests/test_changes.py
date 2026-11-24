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
        # The stage label follows the stage, so only the stage is reported (edge case 22)
        self.assertEqual(out["per_deal"]["d01"], [{"field": "stage", "before": "qualification", "after": "proposal"}])
        # d02's own value and currency are the same, so only the exchange rate moved (edge case 6)
        self.assertEqual(out["per_deal"]["d02"], [{"field": "base_value", "before": "500.0", "after": "2000.5",
                                                   "cause": "exchange rate"}])
        self.assertEqual(out["per_deal"]["d03"], [{"field": "expected_close_date",
                                                   "before": "2026-12-15", "after": "2027-01-10"}])

    def test_value_change_is_not_an_exchange_rate_change(self):
        changes.save_snapshot(rows(deal(2, value=500.0, _base_value=500.0)), "keystone", LAST_WEEK, self.folder)
        out = changes.compare(rows(deal(2)), self.folder, "keystone", TODAY)
        self.assertEqual([c["field"] for c in out["per_deal"]["d02"]], ["value", "base_value"])
        self.assertTrue(all("cause" not in c for c in out["per_deal"]["d02"]))

    def test_renamed_stage_label_is_not_a_change(self):
        changes.save_snapshot([replace(r, stage_label="Old name") for r in rows(deal(1))],
                              "keystone", LAST_WEEK, self.folder)
        self.assertEqual(changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)["per_deal"], {})

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
                          deal(4, value=10.0, _base_value=10.0), deal(6))   # d05 is gone
        shown = [r for r in everything if r.id in ("d01", "d06")]
        reasons = {d["id"]: d["reason"] for d in
                   changes.compare(shown, self.folder, "keystone", TODAY, all_rows=everything)["dropped"]}
        self.assertEqual(reasons, {"d02": "closed won", "d03": "stage not recognised", "d04": "value changed",
                                   "d05": "no longer in the CRM (deleted, or not visible to this seat)"})

    def test_outranked_after_exchange_rate_change(self):
        everything = rows(deal(1), deal(2), deal(3), deal(4, _base_value=10.0), deal(5))  # same value, new rate
        shown = [r for r in everything if r.id != "d04"]
        out = changes.compare(shown, self.folder, "keystone", TODAY, all_rows=everything)
        self.assertEqual(out["dropped"], [{"id": "d04", "title": "Deal 4",
                                           "reason": "outranked after an exchange-rate change"}])

    def test_outranked(self):
        everything = rows(deal(1), deal(2), deal(3), deal(4), deal(5), deal(9))
        shown = [r for r in everything if r.id != "d05"]
        out = changes.compare(shown, self.folder, "keystone", TODAY, all_rows=everything)
        self.assertEqual(out["dropped"][0]["reason"], "outranked by other deals")


    def test_no_usable_pipeline_is_outranked_not_unknown(self):
        # Sunil's review, point 1: a deal with no pipeline, or a pipeline that doesn't exist, is
        # open and ranked (OBS-30-001), so when pushed out it is "outranked", not "stage not recognised"
        for pipeline in (None, "no_such_pipeline"):
            with self.subTest(pipeline=pipeline):
                folder = self.folder / f"p-{pipeline}"
                folder.mkdir()
                before = rows(deal(2, pipeline=pipeline), deal(1))
                changes.save_snapshot(before, "keystone", LAST_WEEK, folder)
                everything = rows(deal(9), deal(2, pipeline=pipeline), deal(1))
                self.assertFalse(everything[1].stage_known)
                out = changes.compare(everything[:2], folder, "keystone", TODAY, all_rows=everything, top=2)
                self.assertEqual(out["dropped"], [{"id": "d01", "title": "Deal 1", "reason": "outranked by other deals"}])
                out = changes.compare([everything[0], everything[2]], folder, "keystone", TODAY,
                                      all_rows=everything, top=2)
                self.assertEqual(out["dropped"], [{"id": "d02", "title": "Deal 2", "reason": "outranked by other deals"}])


    def test_left_out_and_moved_deals_are_not_outranked(self):
        # Bug hunt BH-05: base value unchanged, so these used to read "outranked by other deals".
        cases = {"left out: value zero or less": {"value": 0},
                 "left out: value missing or not readable": {"value": None},
                 "left out: no usable base value": {"_base_currency": "??"},
                 "moved to another company or currency list": {"company_id": "c2"}}
        for reason, over in cases.items():
            with self.subTest(reason=reason):
                everything = rows(deal(1), deal(2), deal(3), deal(4), deal(5, **over))
                out = changes.compare(everything[:4], self.folder, "keystone", TODAY, all_rows=everything)
                self.assertEqual(out["dropped"], [{"id": "d05", "title": "Deal 5", "reason": reason}])

class WholeRanking(Base):
    """Edge case 7: the snapshot holds every ranked deal, so --top can change between runs."""
    def setUp(self):
        super().setUp()
        ranked = rows(deal(5), deal(4), deal(3, stage="qualification"), deal(2), deal(1))
        changes.save_snapshot(ranked[:2], "keystone", LAST_WEEK, self.folder, ranked=ranked)

    def test_file_holds_ranking_and_shown_ids(self):
        payload = json.loads((self.folder / "keystone-2026-11-23.json").read_text())
        self.assertEqual([r["id"] for r in payload["rows"]], ["d05", "d04", "d03", "d02", "d01"])
        self.assertEqual(payload["shown"], ["d05", "d04"])

    def test_larger_top_gives_no_false_entered(self):
        now = rows(deal(5), deal(4), deal(3))
        out = changes.compare(now, self.folder, "keystone", TODAY, top=3)
        self.assertEqual((out["entered"], out["dropped"]), ([], []))
        # d03 was not shown last week, but its history is there
        self.assertEqual(out["per_deal"]["d03"][0], {"field": "stage", "before": "qualification", "after": "proposal"})

    def test_smaller_top_gives_no_false_dropped(self):
        out = changes.compare(rows(deal(5)), self.folder, "keystone", TODAY, top=1)
        self.assertEqual((out["entered"], out["dropped"]), ([], []))

    def test_without_top_last_week_is_what_was_shown(self):
        out = changes.compare(rows(deal(5), deal(4), deal(3)), self.folder, "keystone", TODAY)
        self.assertEqual((out["entered"], out["dropped"]), (["d03"], []))

    def test_top_counts_per_company_and_base_currency(self):
        # Sunil's review, point 2: deals.top() takes the first n of each company and base
        # currency, so last week's list is cut the same way
        folder = self.folder / "groups"
        folder.mkdir()
        ranked = rows(deal(8), deal(7), deal(6, company_id="c2"), deal(5, company_id="c2"),
                      deal(4, _base_currency="USD"), deal(3, _base_currency="USD"))
        changes.save_snapshot(ranked, "keystone", LAST_WEEK, folder, ranked=ranked)
        shown = [r for r in ranked if r.id in ("d08", "d06", "d04")]       # top 1 of each group
        out = changes.compare(shown, folder, "keystone", TODAY, all_rows=ranked, top=1)
        self.assertEqual((out["entered"], out["dropped"]), ([], []))
        # the second company's leader is outranked within its own group only
        now = rows(deal(8), deal(7), deal(9, company_id="c2", title="New c2 deal"), deal(6, company_id="c2"),
                   deal(4, _base_currency="USD"))
        shown = [now[0], now[2], now[4]]
        out = changes.compare(shown, folder, "keystone", TODAY, all_rows=now, top=1)
        self.assertEqual(out["entered"], ["d09"])
        self.assertEqual(out["dropped"], [{"id": "d06", "title": "Deal 6", "reason": "outranked by other deals"}])

    def test_first_per_group_keeps_order(self):
        ranked = rows(deal(9), deal(8, company_id="c2"), deal(7), deal(6, company_id="c2"), deal(5))
        self.assertEqual([r.id for r in changes.first_per_group(ranked, 2)], ["d09", "d08", "d07", "d06"])

    def test_shown_deal_missing_from_ranking_is_kept(self):
        changes.save_snapshot(rows(deal(9)), "keystone", TODAY, self.folder, ranked=rows(deal(1)))
        payload = json.loads((self.folder / "keystone-2026-11-30.json").read_text())
        self.assertEqual(([r["id"] for r in payload["rows"]], payload["shown"]), (["d01", "d09"], ["d09"]))

    def test_bad_shown_list_stops_the_run(self):
        path = self.folder / "keystone-2026-11-23.json"
        payload = json.loads(path.read_text())
        for bad in (["d99"], ["d05", "d05"], "d05", [5]):
            payload["shown"] = bad
            path.write_text(json.dumps(payload))
            with self.assertRaises(changes.SnapshotError):
                changes.compare(rows(deal(5)), self.folder, "keystone", TODAY)

    def test_bad_top(self):
        for top in (0, -1, True, "10", 2.5):
            with self.assertRaises(ValueError):
                changes.compare(rows(deal(5)), self.folder, "keystone", TODAY, top=top)


class SinceAndFolders(Base):
    """Edge case 8: --since, test runs and future-dated snapshots."""
    def test_since_uses_exactly_that_day(self):
        changes.save_snapshot(rows(deal(1, stage="new")), "keystone", date(2026, 11, 16), self.folder)
        changes.save_snapshot(rows(deal(1, stage="negotiation")), "keystone", LAST_WEEK, self.folder)
        out = changes.compare(rows(deal(1)), self.folder, "keystone", TODAY, since=date(2026, 11, 16))
        self.assertEqual(out["previous_date"], "2026-11-16")
        self.assertEqual(out["per_deal"]["d01"][0]["before"], "new")

    def test_since_with_no_snapshot_stops(self):
        changes.save_snapshot(rows(deal(1)), "keystone", LAST_WEEK, self.folder)
        with self.assertRaises(changes.NoSnapshot):
            changes.compare(rows(deal(1)), self.folder, "keystone", TODAY, since=date(2026, 11, 22))

    def test_since_must_be_before_the_brief(self):
        for since in (TODAY, date(2026, 12, 1)):
            with self.assertRaises(ValueError):
                changes.compare(rows(deal(1)), self.folder, "keystone", TODAY, since=since)

    def test_official_folder_refuses_future_snapshot(self):
        with self.assertRaises(changes.SnapshotError):
            changes.save_snapshot(rows(deal(1)), "keystone", date(2026, 12, 10), self.folder, not_after=TODAY)
        self.assertEqual(list(self.folder.iterdir()), [])
        changes.save_snapshot(rows(deal(1)), "keystone", TODAY, self.folder, not_after=TODAY)

    def test_folder_rule(self):
        seat = Path(changes.__file__).resolve().parent.parent
        self.assertEqual(changes.snapshot_folder(), seat / "briefs/snapshots")
        self.assertEqual(changes.snapshot_folder(date_given=True), seat / "briefs/test-snapshots")
        self.assertEqual(changes.snapshot_folder(prompt="close them"), seat / "briefs/test-snapshots")
        self.assertEqual(changes.snapshot_folder("x/y", date_given=True), Path("x/y"))   # given: as given

    def test_default_folders_do_not_depend_on_the_working_directory(self):
        # Bug hunt BH-07: run from the repo root, the snapshots went to an untracked briefs/ there.
        seat = Path(changes.__file__).resolve().parent.parent
        here = os.getcwd()
        try:
            os.chdir(self.folder)
            for folder in (changes.snapshot_folder(), changes.snapshot_folder(date_given=True)):
                self.assertTrue(folder.is_absolute())
                self.assertEqual(folder.parent.parent, seat)
        finally:
            os.chdir(here)


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

    def _rewrite_rows(self, path, change):
        payload = json.loads(path.read_text())
        for r in payload["rows"]:
            change(r)
        path.write_text(json.dumps(payload))

    def test_older_snapshot_without_new_fields_still_loads(self):
        # edge case 5: a DealRow field added after the file was written loads as None
        path = changes.save_snapshot(rows(deal(1), deal(2)), "keystone", LAST_WEEK, self.folder)
        self._rewrite_rows(path, lambda r: (r.pop("rot_level"), r.pop("fixture_flag")))
        day, loaded = changes.latest_before(self.folder, "keystone", TODAY)
        self.assertEqual((day, loaded[0].rot_level), (LAST_WEEK, None))
        out = changes.compare(rows(deal(1), deal(2)), self.folder, "keystone", TODAY)
        self.assertEqual((out["per_deal"], out["entered"], out["dropped"]), ({}, [], []))

    def test_tracked_field_missing_from_older_snapshot_is_not_a_change(self):
        # a tracked field the old file never saved is unknown, not empty: no false change
        path = changes.save_snapshot(rows(deal(1, stage="new")), "keystone", LAST_WEEK, self.folder)
        self._rewrite_rows(path, lambda r: r.pop("probability"))
        out = changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)
        self.assertEqual([c["field"] for c in out["per_deal"]["d01"]], ["stage"])

    def test_saved_none_is_still_a_change(self):
        # a field the old file saved as empty is known to have been empty
        changes.save_snapshot(rows(deal(1, probability=None)), "keystone", LAST_WEEK, self.folder)
        out = changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)
        self.assertEqual(out["per_deal"]["d01"], [{"field": "probability", "before": None, "after": "40.0"}])

    def test_newer_snapshot_with_extra_fields_still_loads(self):
        path = changes.save_snapshot(rows(deal(1)), "keystone", LAST_WEEK, self.folder)
        self._rewrite_rows(path, lambda r: r.update(field_from_a_later_version="x"))
        self.assertEqual(changes.compare(rows(deal(1)), self.folder, "keystone", TODAY)["per_deal"], {})

    def test_snapshot_without_format_stops_the_run(self):
        path = changes.save_snapshot(rows(deal(1)), "keystone", LAST_WEEK, self.folder)
        payload = json.loads(path.read_text())
        del payload["format"]
        path.write_text(json.dumps(payload))
        with self.assertRaises(changes.SnapshotError):
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
