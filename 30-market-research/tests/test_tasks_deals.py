import io
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock
from datetime import date

from brief import debug, deals, tasks_deals
from brief.rows import to_dict
from tests.fakes import FakeClient, deal

AS_OF = date(2026, 11, 30)


def data():
    """12 open deals (two tied), one closed, one unknown stage, one zero value, one unknown currency."""
    records = [deal(i) for i in range(1, 13)]
    records[0]["_base_value"] = records[1]["_base_value"]     # d01 ties d02
    records += [deal(20, stage="closed_won", _base_value=10 ** 9), deal(21, stage="weird", _base_value=10 ** 9),
                deal(22, value=0), deal(23, currency="ZZZ", _base_value=None), deal(24, title="T6-probe")]
    return records


def honest_build(client, instance, as_of, top=10, snapshots=None, **_):
    ranked, issues = deals.open_deals(tasks_deals._rows(client))
    tied = deals.tied_beyond(ranked, top)
    groups = [{"company_id": c, "base_currency": cur, "tied_beyond": tied.get((c, cur), 0)}
              for c, cur, _ in deals.groups(ranked)]
    return {"deals": [{"row": to_dict(r)} for r in deals.top(ranked, top)], "data_issues": issues, "groups": groups}


def broken(change):
    def build(client, instance, as_of, top=10, snapshots=None, **_):
        out = honest_build(client, instance, as_of, top=top)
        change(out)
        return out
    return build


class TasksDealsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctx = {"instance": "suryodaya", "as_of": AS_OF, "workdir": self.tmp.name}
        self.client = FakeClient(data())

    def tearDown(self):
        debug.ENABLED = False
        self.tmp.cleanup()

    def run_all(self, build=None):
        return {tid: fn(self.client, self.ctx, build=build) for tid, _, fn in tasks_deals.TASKS}

    def test_task_ids_in_suite_order(self):
        self.assertEqual([t[0] for t in tasks_deals.TASKS],
                         ["top10_ids", "open_only", "unknown_stage", "currency_safe", "zero_value", "tie_break"])

    def test_honest_brief_passes_all(self):
        for tid, r in self.run_all(honest_build).items():
            self.assertTrue(r["passed"], (tid, r))
            self.assertEqual(r["score"], 1.0)

    @mock.patch.dict("sys.modules", {"brief.report": None})   # component mode: as before report.py existed
    def test_component_mode_is_capped(self):
        for tid, r in self.run_all().items():     # report.py doesn't exist yet: deals.py directly
            self.assertTrue(r["passed"], (tid, r))
            self.assertEqual(r["score"], 0.5)
            self.assertIn("mode=component", r["evidence"])

    def test_no_titles_in_evidence(self):
        for r in self.run_all(honest_build).values():
            self.assertNotIn("Deal ", r["evidence"])

    def test_recount_is_independent(self):
        c = tasks_deals.recount(tasks_deals._rows(self.client))
        self.assertEqual(c["closed"], {"d20"})
        self.assertEqual(c["unknown_stage"], {"d21"})
        self.assertEqual(c["value_not_positive"], {"d22"})
        self.assertEqual(c["currency"], {"d23"})
        self.assertEqual(tasks_deals.expected_top(c)[("c1", "INR")][:4], ["d24", "d12", "d11", "d10"])

    def test_wrong_order_fails_task1_with_partial_credit(self):
        def swap(out):
            out["deals"][0], out["deals"][1] = out["deals"][1], out["deals"][0]
        r = tasks_deals.top10_ids(self.client, self.ctx, build=broken(swap))
        self.assertFalse(r["passed"])
        self.assertEqual(r["score"], 0.8)

    def test_too_many_deals_fails_task1(self):
        r = tasks_deals.top10_ids(self.client, self.ctx,
                                  build=lambda c, i, a, top=10, **k: honest_build(c, i, a, top=11))
        self.assertFalse(r["passed"])
        self.assertEqual(r["score"], round(10 / 11, 2))     # every position right, one extra: not full marks

    def test_no_pipeline_deals_left_out_fails_task2(self):
        def stage_known_only(client, instance, as_of, top=10, **_):
            ranked, issues = deals.open_deals(tasks_deals._rows(client))
            ranked = [r for r in ranked if r.stage_known]
            return {"deals": [{"row": to_dict(r)} for r in deals.top(ranked, top)], "data_issues": issues}
        r = tasks_deals.open_only(self.client, self.ctx, build=stage_known_only)
        self.assertFalse(r["passed"])
        self.assertIn("no_pipeline_seeds_missing=2", r["evidence"])

    def test_closed_deal_shown_fails_task2(self):
        def ranks_closed(client, instance, as_of, top=10, **_):
            rows = [r for r in tasks_deals._rows(client) if r.base_value]
            rows.sort(key=lambda r: (-r.base_value, r.id))
            return {"deals": [{"row": to_dict(r)} for r in rows[:top]], "data_issues": []}
        r = tasks_deals.open_only(self.client, self.ctx, build=ranks_closed)
        self.assertFalse(r["passed"])
        self.assertIn("seeded_shown=2", r["evidence"])

    def test_unlisted_unknown_stage_fails_task3(self):
        def drop(out):
            out["data_issues"] = [i for i in out["data_issues"] if i["issue"] != deals.LEFT_OUT_STAGE
                                  and not i["issue"].startswith("stage")]
        r = tasks_deals.unknown_stage(self.client, self.ctx, build=broken(drop))
        self.assertFalse(r["passed"])
        self.assertEqual(r["score"], 0.0)

    def test_ranking_on_own_value_fails_task4(self):
        def by_value(client, instance, as_of, top=10, **_):
            ranked, issues = deals.open_deals(tasks_deals._rows(client))
            ranked.sort(key=lambda r: (-r.value, r.id))
            return {"deals": [{"row": to_dict(r)} for r in ranked[:top]], "data_issues": issues}
        r = tasks_deals.currency_safe(self.client, self.ctx, build=by_value)
        self.assertFalse(r["passed"])
        self.assertIn("ranking differs", r["evidence"])

    def test_zero_value_judged_on_base_value_fails_task5(self):
        def on_base(client, instance, as_of, top=10, **_):
            rows = [r for r in tasks_deals._rows(client)
                    if r.stage_known and r.stage not in deals.CLOSED and r.base_value]
            rows.sort(key=lambda r: (-r.base_value, r.id))
            return {"deals": [{"row": to_dict(r)} for r in rows[:top]], "data_issues": []}
        r = tasks_deals.zero_value(self.client, self.ctx, build=on_base)
        self.assertFalse(r["passed"])

    def test_tie_at_cut_off_not_reported_fails_task6(self):
        def no_ties(out):
            for g in out["groups"]:
                g["tied_beyond"] = 0
        r = tasks_deals.tie_break(self.client, self.ctx, build=broken(no_ties))
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("cut-off not reported", r["evidence"])

    def test_real_brief_passes_tasks_1_to_6_in_full_mode(self):
        for tid, title, fn in tasks_deals.TASKS:
            r = fn(self.client, self.ctx)
            self.assertEqual((r["passed"], r["score"]), (True, 1.0), (tid, r["evidence"]))

    def test_numeric_tie_break_fails_task6(self):
        def numeric(client, instance, as_of, top=10, **_):
            ranked, issues = deals.open_deals(tasks_deals._rows(client))
            ranked.sort(key=lambda r: (-r.base_value, int(r.id.rsplit("-", 1)[-1]) if r.id.startswith(tasks_deals.SEED + "tie-")
                                       else 0, r.id))
            return {"deals": [{"row": to_dict(r)} for r in ranked[:top]], "data_issues": issues}
        r = tasks_deals.tie_break(self.client, self.ctx, build=numeric)
        self.assertFalse(r["passed"])
        self.assertIn("deal-id order", r["evidence"])

    def test_read_order_dependence_fails_task6(self):
        def unstable(client, instance, as_of, top=10, **_):
            ranked, issues = deals.open_deals(tasks_deals._rows(client))
            order = {r.id: i for i, r in enumerate(tasks_deals._rows(client))}
            ranked.sort(key=lambda r: (-r.base_value, order[r.id]))     # ties keep read order
            return {"deals": [{"row": to_dict(r)} for r in ranked[:top]], "data_issues": issues}
        r = tasks_deals.tie_break(self.client, self.ctx, build=unstable)
        self.assertFalse(r["passed"])

    def test_seeds_are_never_written(self):
        self.run_all(honest_build)
        self.assertFalse(any(d["id"].startswith(tasks_deals.SEED) for d in self.client.deals))

    def test_no_eligible_deal_is_infrastructure(self):
        client = FakeClient([deal(1, value=0)])
        for tid, _, fn in tasks_deals.TASKS:
            r = fn(client, self.ctx, build=honest_build)
            self.assertFalse(r["passed"])
            self.assertTrue(r["evidence"].startswith("infrastructure") or "infrastructure" in r["evidence"], tid)

    def test_crash_is_infrastructure(self):
        def boom(*a, **k):
            raise RuntimeError("boom")
        for tid, _, fn in tasks_deals.TASKS:
            r = fn(self.client, self.ctx, build=boom)
            self.assertEqual((r["passed"], r["score"]), (False, 0.0), tid)
            self.assertIn("infrastructure: RuntimeError", r["evidence"])

    def test_debug_prints_traceback_and_want_got_only_when_enabled(self):
        def boom(*a, **k):
            raise RuntimeError("boom")

        def swap(out):
            out["deals"][0], out["deals"][1] = out["deals"][1], out["deals"][0]

        quiet = io.StringIO()
        with redirect_stderr(quiet):
            tasks_deals.top10_ids(self.client, self.ctx, build=boom)
            tasks_deals.top10_ids(self.client, self.ctx, build=broken(swap))
        self.assertEqual(quiet.getvalue(), "")

        debug.ENABLED = True
        debug.current = "top10_ids"
        loud = io.StringIO()
        with redirect_stderr(loud):
            r_crash = tasks_deals.top10_ids(self.client, self.ctx, build=boom)
            r_wrong = tasks_deals.top10_ids(self.client, self.ctx, build=broken(swap))
        debug.current = ""
        text = loud.getvalue()
        self.assertIn("debug: traceback in top10_ids:", text)
        self.assertIn("RuntimeError: boom", text)
        self.assertIn("debug: top10_ids", text)
        self.assertIn("want", text)
        self.assertIn("got", text)
        self.assertNotIn("Deal ", r_crash["evidence"])
        self.assertNotIn("Deal ", r_wrong["evidence"])


if __name__ == "__main__":
    unittest.main()
