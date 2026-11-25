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


# ---------------------------------------------------------------- platform stats and the run record (25 Nov)

import json
import tempfile
import urllib.error
from pathlib import Path

from brief import client as client_module


class Flaky(client_module.Client):
    """Fails `fails` times with HTTP 503, then answers. No network, no real waiting."""
    def __init__(self, fails=0):
        super().__init__("http://x", "tok-SECRET-123")
        self.fails, self._sleep = fails, lambda s: None

    def _post(self, body):
        if self.fails:
            self.fails -= 1
            raise urllib.error.HTTPError("http://x/api/mcp", 503, "Service Unavailable", {}, None)
        result = {"structuredContent": {"data": [], "total": 0}}
        return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": self._id, "result": result})


class PlatformStats(unittest.TestCase):
    def tearDown(self):
        debug.ENABLED = False

    def test_requests_retries_and_tools_are_counted(self):
        c = Flaky(fails=2)
        c.call("Deal.list")
        c.call("Party.list")
        s = client_module.stats_of(c)
        self.assertEqual((s["requests"], s["retries"]), (4, 2))
        self.assertEqual(s["waited"], 3.0)                         # 1 s, then 2 s
        self.assertEqual(dict(s["by_tool"]), {"Deal.list": 1, "Party.list": 1})

    def test_debug_names_each_retry_and_a_blocked_write(self):
        debug.ENABLED = True
        c, err = Flaky(fails=1), io.StringIO()
        with redirect_stderr(err):
            c.call("Deal.list")
            with self.assertRaises(client_module.WriteBlocked):
                c.call("Deal.update", {"id": "d01"})
        self.assertIn("debug: retry 1 of tools/call Deal.list after HTTP 503 Service Unavailable, waiting 1 s",
                      err.getvalue())
        self.assertIn("debug: blocked: Deal.update is not a read tool, nothing was sent", err.getvalue())
        self.assertNotIn("tok-SECRET-123", err.getvalue())

    def test_quiet_without_debug(self):
        err = io.StringIO()
        with redirect_stderr(err):
            Flaky(fails=1).call("Deal.list")
        self.assertEqual(err.getvalue(), "")

    def test_a_fake_client_without_stats_counts_zero(self):
        self.assertEqual(client_module.stats_of(FakeClient([]))["requests"], 0)
        self.assertEqual(client_module.stats_of(None)["requests"], 0)


class RunRecord(unittest.TestCase):
    def tearDown(self):
        debug.ENABLED = False

    def test_meta_path_sits_beside_results(self):
        self.assertEqual(suite.meta_path("runs/results.json"), Path("runs/results.meta.json"))
        self.assertEqual(suite.meta_path("out"), Path("out.meta.json"))

    def test_timings_per_task(self):
        timings = {}
        tasks = [("t", "T", lambda c, ctx: {"passed": True, "score": 1.0, "evidence": "ok"}, None)]
        with mock.patch.object(suite, "all_tasks", lambda: tasks):
            suite.run_suite(FakeClient([deal(1)]), "keystone", date(2026, 11, 30), timings=timings)
        self.assertEqual(set(timings), {"keystone:t"})
        self.assertEqual(set(timings["keystone:t"]), {"seconds", "calls", "retries"})

    def test_main_writes_the_meta_file_without_the_token_and_results_keep_their_shape(self):
        class Platform(Flaky):
            def __init__(self):
                super().__init__(fails=1)

        def only_task(c, ctx):
            c.call("Deal.list")
            return {"passed": True, "score": 1.0, "evidence": "ok"}
        env = {"AGENTSWITCH_BASE_URL": "https://example.invalid", "AGENTSWITCH_TOKEN": "tok-SECRET-123"}
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", env, clear=True), \
                mock.patch.object(suite, "Client", Platform), \
                mock.patch.object(suite, "all_tasks", lambda: [("next_step_rule", "T10", only_task, None)]), \
                mock.patch("sys.stdout", io.StringIO()), redirect_stderr(err):
            code = suite.main(["--instance", "keystone", "--date", "2026-11-30", "--out", f"{d}/results.json", "--debug"])
            results = json.loads(Path(d, "results.json").read_text())
            raw_meta = Path(d, "results.meta.json").read_text()
        meta = json.loads(raw_meta)
        self.assertEqual(code, 0)
        self.assertEqual(set(results), {"tasks", "summary"})        # TASK_FORMAT.md shape, unchanged
        self.assertEqual(meta["instance"], "keystone")
        self.assertEqual(meta["tasks"]["keystone:next_step_rule"]["calls"], 1)
        self.assertEqual(meta["platform"]["retries"], 1)             # the first tools/list was retried
        self.assertTrue(meta["debug"])
        for key in ("started", "finished", "seconds", "commit", "login", "python"):
            self.assertIn(key, meta)
        self.assertNotIn("tok-SECRET-123", raw_meta)
        self.assertNotIn("example.invalid", raw_meta)                # no platform address either
        self.assertIn("debug: platform: ", err.getvalue())
        self.assertIn("debug: next_step_rule: pass 1.00 in ", err.getvalue())
        self.assertIn("1 platform calls, 0 retries", err.getvalue())


if __name__ == "__main__":
    unittest.main()
