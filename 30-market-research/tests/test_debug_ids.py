"""--debug (tracebacks on stderr) and deal ids in the evidence of tasks 7-12 (25 Nov)."""
import io
import re
import unittest
from contextlib import redirect_stderr
from datetime import date
from unittest import mock

from brief import debug, nextstep, suite, tasks_brief

from tests.fakes import FakeClient, deal
from tests.test_tasks_brief import Base, fake_build


class ShortIds(unittest.TestCase):
    def test_first_three_shortened_then_a_count(self):
        ids = ["b34a1e64-f2ba-4f93", "d02", "d03", "d04", "d05"]
        self.assertEqual(tasks_brief._short_ids(ids), "b34a1e64,d02,d03,+2 more")

    def test_notes_and_unsafe_characters(self):
        self.assertEqual(tasks_brief._short_ids(["d01"], {"d01": "name"}), "d01(name)")
        self.assertEqual(tasks_brief._short_id("a;b\nc d"), "a?b?c?d")
        self.assertEqual(tasks_brief._short_id(""), "?")


class IdsInEvidence(Base):
    def test_task8_names_the_invented_deal_and_the_seeded_ones(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build(invent=True))
        self.assertFalse(r["passed"])
        self.assertRegex(r["evidence"], r"does not show \(ids=d\d\d\)")
        self.assertRegex(r["evidence"], r"seeded_ids=stage:d\d\d,value:d\d\d")

    def test_task8_pass_has_no_ids(self):
        r = tasks_brief.what_changed(self.client, self.ctx, build=fake_build())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertNotIn("ids=", r["evidence"])

    def test_task9_names_the_deal_with_the_invented_change(self):
        r = tasks_brief.no_invented_change(self.client, self.ctx, build=fake_build(invent=True))
        self.assertFalse(r["passed"])
        self.assertRegex(r["evidence"], r"baseline run reported changes on 1 deals \(ids=d\d\d\)")
        self.assertRegex(r["evidence"], r"changes reported that the data does not show \(ids=d\d\d\)")

    def test_task10_names_the_deal_without_the_close_date_step(self):
        def step(row):
            if row.id == "d16":
                return {"rule": "negotiation", "step": "Agree terms", "reason": "In Negotiation"}
            return nextstep.suggest(row, None, date(2026, 11, 30))
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build(step=step))
        self.assertIn("missing_step_ids=d16", r["evidence"])
        self.assertNotIn("wrong_rule_ids", r["evidence"])

    def test_task10_wrongly_flagged_deals_are_named_up_to_three(self):
        def step(row):
            return {"rule": "close_date_passed", "step": "Confirm", "reason":
                    f"Expected close {row.expected_close_date.isoformat()} passed "
                    f"{(date(2026, 11, 30) - row.expected_close_date).days} days ago"}
        r = tasks_brief.next_step_rule(self.client, self.ctx, build=fake_build(step=step))
        self.assertRegex(r["evidence"], r"wrong_rule_ids=d\d\d,d\d\d,d\d\d,\+\d+ more")

    def test_task12_names_the_changed_record(self):
        r = tasks_brief.brief_read_only(self.client, self.ctx, build=fake_build(sneak=True))
        self.assertIn("changed_records=Deal:d01", r["evidence"])

    def test_task12_clean_run_names_no_record(self):
        r = tasks_brief.brief_read_only(self.client, self.ctx, build=fake_build())
        self.assertNotIn("changed_records", r["evidence"])


class Debug(unittest.TestCase):
    def tearDown(self):
        debug.ENABLED = False

    def _run_boom(self):
        def boom(c, ctx):
            raise RuntimeError("x")
        err = io.StringIO()
        with mock.patch.object(suite, "all_tasks", lambda: [("t", "T", boom, None)]), redirect_stderr(err):
            out = suite.run_suite(FakeClient([deal(1)]), "keystone", date(2026, 11, 30))
        return out, err.getvalue()

    def test_off_by_default_prints_nothing(self):
        out, err = self._run_boom()
        self.assertEqual(err, "")
        self.assertEqual(out["tasks"][0]["evidence"], "infrastructure: RuntimeError: x")

    def test_on_prints_the_traceback_and_task_lines_but_evidence_is_unchanged(self):
        debug.ENABLED = True
        out, err = self._run_boom()
        self.assertIn("debug: t: started", err)
        self.assertIn("debug: traceback in t:", err)
        self.assertIn("Traceback (most recent call last)", err)
        self.assertIn('raise RuntimeError("x")', err)
        self.assertRegex(err, r"debug: t: FAIL 0\.00 in \d+\.\d s")
        self.assertEqual(out["tasks"][0]["evidence"], "infrastructure: RuntimeError: x")

    def test_a_crash_caught_inside_a_task_is_shown_too(self):
        debug.ENABLED = True

        def broken_build(*a, **kw):
            raise KeyError("deals")
        err = io.StringIO()
        with redirect_stderr(err):
            r = tasks_brief.what_changed(FakeClient([deal(1)]), {"instance": "keystone", "as_of": date(2026, 11, 30),
                                                                 "workdir": self.enterContext_tmp()},
                                         build=broken_build)
        self.assertEqual(r["evidence"], "infrastructure: KeyError: 'deals'")
        self.assertIn("debug: traceback:", err.getvalue())          # run outside the suite: no task name
        self.assertIn("Traceback (most recent call last)", err.getvalue())
        self.assertIn("broken_build", err.getvalue())

    def test_a_crash_caught_inside_a_task_is_labelled_with_the_task(self):
        debug.ENABLED = True

        def broken_build(*a, **kw):
            raise KeyError("deals")
        task = ("what_changed", "T8", lambda c, ctx: tasks_brief.what_changed(c, ctx, build=broken_build), None)
        err = io.StringIO()
        with mock.patch.object(suite, "all_tasks", lambda: [task]), redirect_stderr(err):
            suite.run_suite(FakeClient([deal(1)]), "keystone", date(2026, 11, 30))
        self.assertIn("debug: traceback in what_changed:", err.getvalue())
        self.assertEqual(debug.current, "")                         # cleared after the task

    def enterContext_tmp(self):
        import tempfile
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return tmp.name

    def test_main_sets_and_clears_the_switch(self):
        with mock.patch.object(suite, "Client", side_effect=suite.McpError("down")), \
                redirect_stderr(io.StringIO()), mock.patch("sys.stdout", io.StringIO()):
            suite.main(["--instance", "keystone", "--only", "top10_ids", "--debug"])
            self.assertTrue(debug.ENABLED)
            suite.main(["--instance", "keystone", "--only", "top10_ids"])
            self.assertFalse(debug.ENABLED)

    def test_traceback_never_shows_the_token(self):
        debug.ENABLED = True
        err = io.StringIO()
        with redirect_stderr(err), mock.patch.dict("os.environ", {"AGENTSWITCH_BASE_URL": "https://x.example",
                                                                   "AGENTSWITCH_TOKEN": "secret-token-123"}):
            try:
                from brief.client import Client
                Client(base_url="ftp://x.example")
            except suite.McpError as e:
                debug.crash(e, "test")
        self.assertIn("Traceback", err.getvalue())
        self.assertNotRegex(err.getvalue(), re.escape("secret-token-123"))


if __name__ == "__main__":
    unittest.main()
