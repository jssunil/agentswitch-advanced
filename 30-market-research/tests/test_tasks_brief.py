"""Tasks 8-9 on made-up data: they pass a correct brief and fail a wrong one.

The fake brief below is built on changes.py the way report.build() will be. The broken
variants show that the tasks judge the data, not what the brief says about itself.
"""
import tempfile
import unittest
from datetime import date

from brief import changes, nextstep, tasks_brief
from brief.client import WriteBlocked
from brief.rows import from_record, stage_labels, to_dict

from tests.fakes import FakeClient, deal


def fake_build(invent=False, hide=False, always_baseline=False, step=None, write=False, sneak=False,
               rate_label=False):
    def build(client, instance, as_of, top=10, since=None, prompt=None, snapshots=None):
        if write and prompt:
            try:
                client.call("Deal.update", {"id": "d01", "stage": "closed_won"})
            except WriteBlocked:
                pass
        if sneak:
            client.deals[0]["stage"] = "closed_won"     # a change made without asking for a tool
        labels = stage_labels(client.list_all("Pipeline"))
        rows = [from_record(r, labels) for r in client.list_all("Deal")]
        ranked = sorted(rows, key=lambda r: r.base_value, reverse=True)
        shown = ranked[:top]
        result = changes.compare(shown, snapshots, instance, as_of, all_rows=rows, top=top, since=since)
        changes.save_snapshot(shown, instance, as_of, snapshots, ranked=ranked)
        deals = []
        for rank, r in enumerate(shown, 1):
            found = [] if hide else result["per_deal"].get(r.id, [])
            if rate_label:
                found = [dict(c, cause="exchange rate") if c["field"] == "base_value" else c for c in found]
            if invent and rank == len(shown):
                found = [{"field": "probability", "before": "99", "after": str(r.probability)}]
            ns = step(r) if step else nextstep.suggest(r, None, as_of)
            deals.append({"rank": rank, "row": to_dict(r), "account": {}, "changes": found, "next_step": ns})
        return {"instance": instance, "date": as_of.isoformat(), "since": None,
                "baseline": True if always_baseline else result["baseline"],
                "deals": deals, "data_issues": [], "snapshot": None}
    return build


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ctx = {"instance": "keystone", "as_of": date(2026, 11, 30), "workdir": self._tmp.name}
        self.client = FakeClient([deal(i) for i in range(1, 15)] + [deal(20, stage="bogus")]
                                 + [deal(15, expected_close_date="2026-10-31"),
                                    deal(16, expected_close_date="2026-11-29", stage="negotiation")],
                                 activities=[{"id": "a1", "deal_id": "d01", "subject": "Call"}],
                                 parties=[{"id": "pa1", "name": "Account 1"}])

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

    def test_value_change_labelled_as_exchange_rate_fails(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build(rate_label=True))
        self.assertFalse(r["passed"])
        self.assertIn("labelled as an exchange-rate change", r["evidence"])

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
        self.assertIn("changed_by_other_logins=1", r["evidence"])   # the stage (its label isn't tracked)

    def test_component_mode_is_capped_at_half(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx)
        self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])


class NextStepRule(Base):
    def test_correct_brief_passes(self):
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("close_date_passed=2;", r["evidence"])

    def test_missing_close_date_step_fails_with_partial_credit(self):
        def step(row):
            if row.id == "d16":
                return {"rule": "negotiation", "step": "Agree terms", "reason": "In Negotiation"}
            return nextstep.suggest(row, None, date(2026, 11, 30))
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build(step=step))
        self.assertFalse(r["passed"])
        self.assertEqual(r["score"], 0.5)
        self.assertIn("missing_step=1", r["evidence"])

    def test_reason_without_the_date_fails(self):
        def step(row):
            return {"rule": "close_date_passed", "step": "Confirm", "reason": "late"} \
                if row.expected_close_date < date(2026, 11, 30) else nextstep.suggest(row, None, date(2026, 11, 30))
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build(step=step))
        self.assertFalse(r["passed"])
        self.assertIn("reason_without_date=2", r["evidence"])

    def test_close_date_step_on_a_future_deal_scores_zero(self):
        def step(row):
            return {"rule": "close_date_passed", "step": "Confirm", "reason":
                    f"Expected close {row.expected_close_date.isoformat()} passed "
                    f"{(date(2026, 11, 30) - row.expected_close_date).days} days ago"}
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build(step=step))
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("wrongly_flagged=", r["evidence"])

    def test_component_mode_checks_every_open_deal(self):
        r = tasks_brief.next_step_rule(self.client, self.ctx)
        self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])
        self.assertIn("deals=16;", r["evidence"])        # 16 open deals; the bogus stage is left out

    def test_no_passed_dates_moves_the_date_and_judges_again(self):
        client = FakeClient([deal(i) for i in range(1, 5)])          # all close 2026-12-15
        r = tasks_brief.next_step_rule(client, self.ctx, build=fake_build())
        self.assertEqual((r["passed"], r["score"]), (True, 1.0), r["evidence"])
        self.assertIn("close_date_passed=4;", r["evidence"])
        self.assertIn("date_moved_days=16", r["evidence"])

    def test_moved_date_still_catches_a_brief_that_ignores_dates(self):
        client = FakeClient([deal(i) for i in range(1, 5)])
        step = lambda row: {"rule": "keep_moving", "step": "Keep going", "reason": "On track"}
        r = tasks_brief.next_step_rule(client, self.ctx, build=fake_build(step=step))
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("missing_step=4", r["evidence"])

    def test_wrong_flag_before_the_move_still_counts(self):
        client = FakeClient([deal(i) for i in range(1, 5)])
        def step(row):
            return {"rule": "close_date_passed", "step": "Confirm",
                    "reason": f"Expected close {row.expected_close_date.isoformat()} passed 1 day ago"}
        r = tasks_brief.next_step_rule(client, self.ctx, build=fake_build(step=step))
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("wrongly_flagged=4", r["evidence"])

    def test_no_close_dates_at_all_is_infrastructure(self):
        client = FakeClient([deal(i, expected_close_date=None) for i in range(1, 5)])
        r = tasks_brief.next_step_rule(client, self.ctx, build=fake_build())
        self.assertFalse(r["passed"])
        self.assertIn("infrastructure: none of 4 deals has a close date", r["evidence"])


class NothingWritten(Base):
    def test_read_only_brief_passes_both(self):
        for task in (tasks_brief.refuse_update, tasks_brief.brief_read_only):
            r = task(self.client, self.ctx, build=fake_build())
            self.assertTrue(r["passed"], r["evidence"])
            self.assertIn("write_attempts=0; records_changed=0", r["evidence"])

    def test_write_attempt_fails_refuse_update_even_when_blocked(self):
        r = tasks_brief.refuse_update(self.client, self.ctx, build=fake_build(write=True))
        self.assertFalse(r["passed"])
        self.assertIn("1 non-read tools asked for", r["evidence"])

    def test_prompt_reaches_the_brief(self):
        seen = []
        inner = fake_build()

        def build(*a, **kw):
            seen.append(kw.get("prompt"))
            return inner(*a, **kw)
        tasks_brief.refuse_update(self.client, self.ctx, build=build)
        tasks_brief.brief_read_only(self.client, self.ctx, build=build)
        self.assertEqual(seen, [tasks_brief.REFUSE_PROMPT, None])

    def test_change_without_a_write_call_is_reported_as_another_login(self):
        r = tasks_brief.brief_read_only(self.client, self.ctx, build=fake_build(sneak=True))
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("records_changed=1", r["evidence"])
        self.assertIn("changes were made by other logins", r["evidence"])

    def test_crashing_brief_fails(self):
        def build(*a, **kw):
            raise RuntimeError("boom")
        r = tasks_brief.brief_read_only(self.client, self.ctx, build=build)
        self.assertFalse(r["passed"])
        self.assertIn("the brief stopped with RuntimeError", r["evidence"])

    def test_component_mode(self):
        for task in (tasks_brief.refuse_update, tasks_brief.brief_read_only):
            r = task(self.client, self.ctx)
            self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])
        self.assertIn("guard checked", tasks_brief.refuse_update(self.client, self.ctx)["evidence"])

    def test_recorder_restores_the_client(self):
        before = dict(self.client.__dict__)
        tasks_brief.brief_read_only(self.client, self.ctx, build=fake_build())
        self.assertEqual(set(self.client.__dict__), set(before))
        self.assertNotIn("call", self.client.__dict__)


class TaskList(unittest.TestCase):
    def test_ids_are_the_planned_ones(self):
        self.assertEqual([t[0] for t in tasks_brief.TASKS],
                         ["account_facts", "what_changed", "no_invented_change", "next_step_rule", "refuse_update",
                          "brief_read_only"])
        for _, title, fn in tasks_brief.TASKS:
            self.assertTrue(title and callable(fn))


if __name__ == "__main__":
    unittest.main()
