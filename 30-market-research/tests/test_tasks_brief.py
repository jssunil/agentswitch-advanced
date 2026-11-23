"""Tasks 8-9 on made-up data: they pass a correct brief and fail a wrong one.

The fake brief below is built on changes.py the way report.build() will be. The broken
variants show that the tasks judge the data, not what the brief says about itself.
"""
import tempfile
import unittest
from datetime import date

from brief import changes, tasks_brief
from brief.rows import from_record, stage_labels, to_dict

from tests.fakes import FakeClient, deal


def fake_build(invent=False, hide=False, always_baseline=False):
    def build(client, instance, as_of, top=10, since=None, prompt=None, snapshots=None):
        labels = stage_labels(client.list_all("Pipeline"))
        rows = [from_record(r, labels) for r in client.list_all("Deal")]
        shown = sorted(rows, key=lambda r: r.base_value, reverse=True)[:top]
        result = changes.compare(shown, snapshots, instance, as_of)
        changes.save_snapshot(shown, instance, as_of, snapshots)
        deals = []
        for rank, r in enumerate(shown, 1):
            found = [] if hide else result["per_deal"].get(r.id, [])
            if invent and rank == len(shown):
                found = [{"field": "probability", "before": "99", "after": str(r.probability)}]
            deals.append({"rank": rank, "row": to_dict(r), "account": {}, "changes": found, "next_step": {}})
        return {"instance": instance, "date": as_of.isoformat(), "since": None,
                "baseline": True if always_baseline else result["baseline"],
                "deals": deals, "data_issues": [], "snapshot": None}
    return build


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ctx = {"instance": "keystone", "as_of": date(2026, 11, 30), "workdir": self._tmp.name}
        self.client = FakeClient([deal(i) for i in range(1, 15)] + [deal(20, stage="bogus")])

    def tearDown(self):
        self._tmp.cleanup()


class WhatChanged(Base):
    def test_correct_brief_passes(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertEqual(r["score"], 1.0)

    def test_hidden_changes_fail(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build(hide=True))
        self.assertFalse(r["passed"])
        self.assertIn("seeded stage change not reported", r["evidence"])

    def test_change_on_an_unseeded_deal_fails(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build(invent=True))
        self.assertFalse(r["passed"])
        self.assertIn("1 unseeded deals reported as changed", r["evidence"])

    def test_component_mode_is_capped_at_half(self):
        r = tasks_brief.what_changed(self.client, self.ctx)
        self.assertTrue(r["passed"], r["evidence"])
        self.assertEqual(r["score"], 0.5)
        self.assertTrue(r["evidence"].startswith("mode=component"))

    def test_no_deals_is_reported_not_crashed(self):
        r = tasks_brief.what_changed(FakeClient([]), self.ctx, build=fake_build())
        self.assertFalse(r["passed"])
        self.assertIn("infrastructure", r["evidence"])

    def test_platform_failure_is_infrastructure(self):
        def broken(client, entity):
            raise ConnectionError("down")
        self.client.on_read = broken
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build())
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertTrue(r["evidence"].startswith("infrastructure: ConnectionError"))

    def test_evidence_has_no_record_text(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build())
        for text in ("Deal 1", "Account", "1000.5"):
            self.assertNotIn(text, r["evidence"])


class NoInventedChange(Base):
    def test_correct_brief_passes(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=fake_build())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("invented=0", r["evidence"])

    def test_invented_change_fails(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=fake_build(invent=True))
        self.assertFalse(r["passed"])
        self.assertIn("baseline run reported changes on 1 deals", r["evidence"])
        self.assertIn("invented=1;", r["evidence"])
        self.assertIn("changes reported that the data does not show", r["evidence"])

    def test_invented_change_on_rerun_only_fails(self):
        normal, inventing = fake_build(), fake_build(invent=True)
        calls = []

        def build(*args, **kw):
            calls.append(1)
            return (normal if len(calls) == 1 else inventing)(*args, **kw)
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=build)
        self.assertFalse(r["passed"])
        self.assertIn("baseline_changes=0; invented=1;", r["evidence"])

    def test_brief_that_is_always_baseline_fails(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=fake_build(always_baseline=True))
        self.assertFalse(r["passed"])
        self.assertIn("run with last week's snapshot was marked baseline", r["evidence"])

    def test_real_edit_by_another_login_is_not_counted_as_invented(self):
        reads = {"n": 0}

        def edit_after_first_run(client, entity):
            if entity == "Deal":
                reads["n"] += 1
                if reads["n"] == 2:   # between the first run and the second
                    client.deals[0]["stage"] = "negotiation"
        self.client.deals[0]["_base_value"] = 99999.0     # make d01 part of the list
        self.client.on_read = edit_after_first_run
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=fake_build())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("changed_by_other_logins=2", r["evidence"])   # stage and its label

    def test_component_mode_is_capped_at_half(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx)
        self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])


class TaskList(unittest.TestCase):
    def test_ids_are_the_planned_ones(self):
        self.assertEqual([t[0] for t in tasks_brief.TASKS], ["what_changed", "no_invented_change"])
        for _, title, fn in tasks_brief.TASKS:
            self.assertTrue(title and callable(fn))


if __name__ == "__main__":
    unittest.main()
