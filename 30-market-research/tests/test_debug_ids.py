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

    def test_debug_output_is_cleaned(self):
        # Bug hunt E2, for --debug: an error message or a value can carry server or record text.
        debug.ENABLED = True
        bad = "x\x1b[2J\x1b]0;owned\x07\u202ey"
        err = io.StringIO()
        with redirect_stderr(err):
            try:
                raise suite.McpError(f"server said {bad}")
            except suite.McpError as e:
                debug.crash(e, f"task {bad}")
            debug.step(f"retry after {bad}")
            debug.diff(f"d{bad}", "stage", bad, None)
        text = err.getvalue()
        self.assertIn("Traceback (most recent call last)", text)
        self.assertIn("server said x[2J]0;ownedy", text)
        for ch in ("\x1b", "\x07", "\u202e"):
            self.assertNotIn(ch, text)


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



# ---------------------------------------------------------------- record and replay (25 Nov)

import os
import stat


class Scripted(client_module.RecordingClient):
    """A recording client whose platform answers from a script: each Deal.list read returns the
    next list of deals; a 401 when `reject` is set. No network."""
    def __init__(self, folder, deal_lists, reject=False):
        super().__init__(folder, "http://x", "tok-SECRET-123")
        self.deal_lists, self.reject, self._sleep = list(deal_lists), reject, lambda s: None

    def _post(self, body):
        msg = json.loads(body)
        if self.reject:
            raise urllib.error.HTTPError("http://x/api/mcp", 401, "Unauthorized", {}, None)
        if msg["method"] == "tools/list":
            result = {"tools": [{"name": "Deal.list"}]}
        else:
            data = self.deal_lists.pop(0) if len(self.deal_lists) > 1 else self.deal_lists[0]
            result = {"structuredContent": {"data": data, "total": len(data)}}
        return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result})


class RecordReplay(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.folder = Path(self._tmp.name, "rec")

    def test_replay_gives_the_same_replies_in_order_without_a_network(self):
        first, second = [{"id": "d01"}], [{"id": "d01"}, {"id": "d02"}]
        rec = Scripted(self.folder, [first, second])
        live = [rec.tool_names(), rec.call("Deal.list"), rec.call("Deal.list"), rec.call("Deal.list")]
        rep = client_module.ReplayClient(self.folder)
        again = [rep.tool_names(), rep.call("Deal.list"), rep.call("Deal.list"), rep.call("Deal.list")]
        self.assertEqual(live, again)
        self.assertEqual(again[1]["data"], first)
        self.assertEqual(again[2]["data"], second)
        self.assertEqual(rep.call("Deal.list")["data"], second)      # out of replies: the last one again
        with self.assertRaises(client_module.McpError):
            rep._post(b"{}")

    def test_a_repeated_identical_reply_is_stored_once(self):
        rec = Scripted(self.folder, [[{"id": "d01", "title": "x" * 1000}]])
        for _ in range(5):
            rec.call("Deal.list")
        lines = (self.folder / "calls.jsonl").read_text().splitlines()
        self.assertEqual(sum(1 for line in lines if '"same": true' in line), 4)
        self.assertLess(len("".join(lines)), 2000)
        rep = client_module.ReplayClient(self.folder)
        self.assertEqual([rep.call("Deal.list")["data"] for _ in range(5)], [[{"id": "d01", "title": "x" * 1000}]] * 5)

    def test_an_error_is_replayed_as_the_same_error(self):
        rec = Scripted(self.folder, [[]], reject=True)
        with self.assertRaises(client_module.McpError) as live:
            rec.call("Deal.list")
        with self.assertRaises(client_module.McpError) as again:
            client_module.ReplayClient(self.folder).call("Deal.list")
        self.assertEqual(str(live.exception), str(again.exception))

    def test_a_request_the_recording_lacks_is_named(self):
        Scripted(self.folder, [[]]).call("Deal.list")
        with self.assertRaises(client_module.McpError) as e:
            client_module.ReplayClient(self.folder).call("Party.list")
        self.assertIn("no recorded reply for tools/call Party.list", str(e.exception))

    def test_writes_are_still_blocked_and_never_recorded(self):
        rec = Scripted(self.folder, [[]])
        with self.assertRaises(client_module.WriteBlocked):
            rec.call("Deal.update", {"id": "d01"})
        self.assertFalse((self.folder / "calls.jsonl").exists())
        with self.assertRaises(client_module.WriteBlocked):
            client_module.ReplayClient.__new__(client_module.ReplayClient).call("Deal.update")

    def test_a_folder_that_holds_a_recording_is_refused(self):
        # Sunil's review, 26 Nov: a second --record on the same folder would append to calls.jsonl.
        Scripted(self.folder, [[{"id": "d01"}]]).call("Deal.list")
        before = (self.folder / "calls.jsonl").read_text()
        with self.assertRaises(client_module.McpError) as e:
            Scripted(self.folder, [[{"id": "d02"}]])
        self.assertIn("already holds a recording", str(e.exception))
        self.assertEqual((self.folder / "calls.jsonl").read_text(), before)
        err = io.StringIO()
        with redirect_stderr(err), self.assertRaises(SystemExit) as stop:
            suite.main(["--instance", "keystone", "--record", str(self.folder)])
        self.assertEqual(stop.exception.code, 2)
        self.assertIn("already holds a recording", err.getvalue())

    def test_recording_is_owner_only_and_holds_no_token(self):
        Scripted(self.folder, [[{"id": "d01"}]]).call("Deal.list")
        self.assertEqual(stat.S_IMODE(os.stat(self.folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.folder / "calls.jsonl").st_mode), 0o600)
        self.assertNotIn("tok-SECRET-123", (self.folder / "calls.jsonl").read_text())

    def test_a_damaged_recording_is_refused(self):
        self.folder.mkdir()
        (self.folder / "calls.jsonl").write_text('{"key": "k", "same": true}\n')
        with self.assertRaises(client_module.McpError) as e:
            client_module.ReplayClient(self.folder)
        self.assertIn("line 1 can't be read", str(e.exception))


class SuiteRecordReplay(unittest.TestCase):
    def test_record_refuses_a_folder_git_would_track(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
            suite.main(["--instance", "keystone", "--record", "brief/never-here"])
        self.assertIn("a folder git would track", err.getvalue())
        self.assertFalse(Path(suite.__file__).parent.joinpath("never-here").exists())

    def test_record_and_replay_together_is_a_usage_error(self):
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            suite.main(["--instance", "keystone", "--record", "/tmp/a", "--replay", "/tmp/b"])

    def test_replay_without_a_recording_is_a_usage_error(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
            suite.main(["--replay", d])
        self.assertIn("no recording", err.getvalue())

    def test_replay_takes_instance_and_date_from_the_recording_and_refuses_another_instance(self):
        with tempfile.TemporaryDirectory() as d:
            Scripted(Path(d), [[]]).tool_names()
            Path(d, "recording.json").write_text(json.dumps({"instance": "suryodaya", "date": "2026-11-24"}))
            seen = {}

            def fake_run_suite(client, instance, as_of, *a, **kw):
                seen.update(instance=instance, as_of=as_of, replay=isinstance(client, client_module.ReplayClient))
                return {"tasks": [], "summary": "suryodaya: 0/0 tasks passed"}
            with mock.patch.object(suite, "run_suite", fake_run_suite), mock.patch("sys.stdout", io.StringIO()), \
                    mock.patch.dict("os.environ", {}, clear=True):
                self.assertEqual(suite.main(["--replay", d]), 0)
            self.assertEqual(seen, {"instance": "suryodaya", "as_of": date(2026, 11, 24), "replay": True})
            with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()) as err:
                suite.main(["--replay", d, "--instance", "keystone"])
            self.assertIn("the recording is of suryodaya", err.getvalue())



# ---------------------------------------------------------------- want/got values and --keep-workdir (25 Nov)

class WantGot(Base):
    def tearDown(self):
        debug.ENABLED = False
        super().tearDown()

    def _err(self, fn, *a, **kw):
        err = io.StringIO()
        with redirect_stderr(err):
            r = fn(*a, **kw)
        return r, err.getvalue()

    def test_quiet_without_debug(self):
        _, err = self._err(tasks_brief.no_invented_change, self.client, self.ctx, build=fake_build(invent=True))
        self.assertEqual(err, "")

    def test_task9_shows_the_invented_change(self):
        debug.ENABLED = True
        r, err = self._err(tasks_brief.no_invented_change, self.client, self.ctx, build=fake_build(invent=True))
        self.assertFalse(r["passed"])
        self.assertRegex(err, r'debug: d\d\d change on a baseline run: want \[\] \| got \[\{"after": ".*", "before": "99", "field": "probability"\}\]')
        self.assertRegex(err, r'debug: d\d\d invented probability change: want \{"now": ".*", "saved": ".*"\} \| got \{"after": ".*", "before": "99"\}')
        self.assertNotIn("want", r["evidence"])                       # values never reach the evidence

    def test_task10_shows_what_was_expected_and_the_step_given(self):
        debug.ENABLED = True

        def step(row):
            if row.id == "d16":
                return {"rule": "negotiation", "step": "Agree terms", "reason": "In Negotiation"}
            return nextstep.suggest(row, None, date(2026, 11, 30))
        _, err = self._err(tasks_brief.next_step_rule, self.client, self.ctx, build=fake_build(step=step))
        self.assertIn('debug: d16 missing_step: want {"as_of": "2026-11-30", "close": "2026-11-29", '
                      '"rule": "close_date_passed"} | got {"reason": "In Negotiation", "rule": "negotiation"', err)

    def test_task10_shows_the_rule_trace_for_a_wrong_deal(self):
        debug.ENABLED = True

        def step(row):
            if row.id == "d16":
                return {"rule": "negotiation", "step": "Agree terms", "reason": "In Negotiation"}
            return nextstep.suggest(row, None, date(2026, 11, 30))
        _, err = self._err(tasks_brief.next_step_rule, self.client, self.ctx, build=fake_build(step=step))
        self.assertIn("d16   rule 1 close_date_passed: matched: expected close 2026-11-29 is before 2026-11-30", err)
        self.assertIn("d16   rule 6 negotiation: not reached, would match: stage is negotiation", err)

    def test_task12_shows_the_changed_fields(self):
        debug.ENABLED = True
        _, err = self._err(tasks_brief.brief_read_only, self.client, self.ctx, build=fake_build(sneak=True))
        self.assertRegex(err, r'debug: Deal:d01 changed: want \{"stage": ".*"\} \| got \{"stage": "closed_won"\}')

    def test_task8_shows_the_seeded_change_it_missed(self):
        debug.ENABLED = True
        _, err = self._err(tasks_brief.what_changed, self.client, self.ctx, build=fake_build(hide=True))
        self.assertRegex(err, r'debug: d\d\d seeded stage change: want \{"after": ".*", "before": "seat30_seeded_stage"\} \| got \[\]')
        self.assertRegex(err, r"debug: d\d\d seeded value change: want ")

    def test_long_values_are_cut_and_labels_keep_the_entity(self):
        debug.ENABLED = True
        err = io.StringIO()
        with redirect_stderr(err):
            debug.diff("Activity:1234567890ab", "subject", "x" * 1000, None)
        self.assertIn("debug: Activity:12345678 subject: want ", err.getvalue())
        self.assertIn("…", err.getvalue())
        self.assertLess(len(err.getvalue()), 400)


class KeepWorkdir(unittest.TestCase):
    def test_kept_and_named_only_when_asked(self):
        import shutil
        seen = []
        tasks = [("t", "T", lambda c, ctx: seen.append(ctx["workdir"]) or {"passed": True, "score": 1.0, "evidence": "ok"},
                  None)]
        err = io.StringIO()
        with mock.patch.object(suite, "all_tasks", lambda: tasks), redirect_stderr(err):
            suite.run_suite(FakeClient([]), "keystone", date(2026, 11, 30))
            suite.run_suite(FakeClient([]), "keystone", date(2026, 11, 30), keep_workdir=True)
        removed, kept = seen
        self.addCleanup(shutil.rmtree, kept, True)
        self.assertFalse(Path(removed).exists())
        self.assertTrue(Path(kept).is_dir())
        self.assertEqual(stat.S_IMODE(os.stat(kept).st_mode), 0o700)
        self.assertIn(f"scratch folder kept (customer records; delete when done): {kept}", err.getvalue())


if __name__ == "__main__":
    unittest.main()
