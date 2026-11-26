import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from brief import __main__ as cli
from brief import debug, deals, explain, nextstep, rows
from brief.client import McpError, RecordingClient
from tests.fakes import PIPELINES, FakeClient, deal

AS_OF = date(2026, 11, 30)


def sample_client():
    records = [deal(i) for i in range(1, 13)]
    records[10]["_base_value"] = records[11]["_base_value"]     # d11 ties d12 at the top
    records[2]["_base_value"] = records[1]["_base_value"]       # d02 ties d03 at the top-10 cut-off
    records += [
        deal(20, stage="closed_won", _base_value=10 ** 9),
        deal(21, stage="closed_lost", _base_value=10 ** 9),
        deal(22, pipeline=None, stage="proposal", _base_value=10 ** 8),
        deal(23, pipeline="no_such_pipe", stage="negotiation", _base_value=9 * 10 ** 7),
        deal(24, stage="mystery_stage", _base_value=10 ** 9),
        deal(25, value="not-a-number", _base_value=10 ** 9),
        deal(26, value=0, _base_value=10 ** 9),
        deal(27, value=-50, _base_value=10 ** 9),
        deal(28, currency="ZZZ", value=5000, _base_value=None),
        deal(29, id="deadbeef-1111-2222-3333-444444444444", title="T9-probe — test\x1b[31m\ndeal",
             expected_close_date="2026-11-01", party_id="pa-red"),
        deal(30, id="ambig-aaaa-1111", party_id="pa-missing"),
        deal(31, id="ambig-aaaa-2222", party_id="pa-missing"),
    ]
    parties = [
        {"id": "pa12", "name": "Acme Corp"},
        {"id": "pa-red", "name": "Secret Name", "_redacted_fields": ["name"]},
    ]
    activities = [
        {"id": "act-done", "deal_id": "deadbeef-1111-2222-3333-444444444444", "type": "call",
         "done": True, "due_date": "2026-11-25", "subject": "Intro\x1b[2J call"},
        {"id": "act-open", "deal_id": "deadbeef-1111-2222-3333-444444444444", "type": "task",
         "done": False, "due_date": "2026-11-10", "subject": "Send terms\r\nnow"},
    ]
    notes = [{"id": "n1", "deal_id": "deadbeef-1111-2222-3333-444444444444", "pinned": True,
              "updated_at": "2026-11-20T10:00:00Z", "title": "Key note"}]
    return FakeClient(records, parties=parties, activities=activities, notes=notes)


class ExplainWalkTest(unittest.TestCase):
    def tearDown(self):
        debug.ENABLED = False

    def test_walk_matches_deals_module_for_every_category(self):
        c = sample_client()
        labels = rows.stage_labels(c.list_all("Pipeline"))
        parsed = [rows.from_record(r, labels) for r in c.list_all("Deal")]
        ranked, _ = deals.open_deals(parsed)
        shown_ids = {r.id for r in deals.top(ranked, 10)}
        ranked_ids = {r.id for r in ranked}

        expected = {
            "d12": "shown",
            "d22": "shown",       # no pipeline: judged by stage key (OBS-30-001)
            "d23": "shown",       # pipeline not found: judged by stage key
            "d01": "outranked",
            "d20": "closed",
            "d21": "closed",
            "d24": "left out",    # unknown stage
            "d25": "left out",    # unreadable value
            "d26": "left out",    # zero value
            "d27": "left out",    # negative value
            "d28": "left out",    # missing base_value
        }
        by_id = {r.id: r for r in parsed}
        for did, want in expected.items():
            steps, walked, actual, _, _ = explain.walk_selection(by_id[did], parsed, top=10)
            self.assertEqual(walked, want, did)
            self.assertEqual(actual, want, did)
            self.assertEqual(did in shown_ids, want == "shown", did)
            self.assertEqual(did in ranked_ids, want in ("shown", "outranked"), did)
            self.assertTrue(steps, did)

    def test_describe_shows_account_facts_nextstep_rules_and_strips_control_chars(self):
        c = sample_client()
        out = io.StringIO()
        code = explain.run(c, "deadbeef", AS_OF, top=10, out=out)   # unique 8-char prefix
        self.assertEqual(code, 0)
        text = out.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertNotIn("AGENT_RESULT", text)
        self.assertIn("id prefix deadbeef", text)
        self.assertIn("Test-data flag: looks like test data", text)
        self.assertIn("not available (redacted)", text)
        self.assertIn("activity source: deal", text)
        self.assertIn("act-open: due 2026-11-10 (OVERDUE)", text)
        self.assertIn("pinned note: Key note", text)
        # Every rule from nextstep.RULES appears in order, with would-match detail after the winner.
        self.assertIn("[matched] close_date_passed:", text)
        self.assertIn("[not reached, would match] overdue_activity:", text)
        for rule in nextstep.RULES:
            self.assertIn(f"] {rule}:", text)

    def test_not_found_and_ambiguous_prefix_exit_5(self):
        c = sample_client()
        for wanted, phrase in (("no-such-deal", "not the id of any deal"),
                               ("ambig-aa", "matches 2 deals")):
            out = io.StringIO()
            self.assertEqual(explain.run(c, wanted, AS_OF, out=out), explain.EXIT_NOT_FOUND)
            self.assertIn(phrase, out.getvalue())

    def test_closed_deal_explains_not_open(self):
        c = sample_client()
        out = io.StringIO()
        self.assertEqual(explain.run(c, "d20", AS_OF, out=out), 0)
        text = out.getvalue()
        self.assertIn("Selection verdict (top 10): CLOSED", text)
        self.assertIn("[matched] not_open:", text)


class ExplainCliTest(unittest.TestCase):
    def tearDown(self):
        debug.ENABLED = False

    def run_cli(self, *args, c=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "Client", lambda: c or sample_client()), \
                redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_cli_prints_explanation_without_agent_result_or_instance(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, out, err = self.run_cli("--explain", "d12", "--date", "2026-11-30")
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Selection verdict (top 10): SHOWN", out)
        self.assertNotIn("AGENT_RESULT", out)

    def test_incompatible_options_exit_2(self):
        for bad in (["--explain", "d12", "--prompt", "top 5"],
                    ["--explain", "d12", "--out", tempfile.mkdtemp()],
                    ["--explain", "d12", "--since", "2026-11-23"],
                    ["--explain", "d12", "--snapshots", tempfile.mkdtemp()],
                    ["--explain", "d12", "--record", tempfile.mkdtemp()],
                    ["--explain", "   "]):
            with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
                cli.main(bad)
            self.assertEqual(e.exception.code, 2, bad)

    def test_unknown_deal_exits_5(self):
        code, out, _ = self.run_cli("--explain", "missing-id", "--date", "2026-11-30")
        self.assertEqual(code, 5)
        self.assertNotIn("AGENT_RESULT", out)

    def test_platform_failure_exits_3(self):
        class Down(FakeClient):
            def list_all(self, entity, filters=None):
                raise McpError("platform unreachable")

        err = io.StringIO()
        with self.assertRaises(SystemExit) as e, redirect_stdout(io.StringIO()), redirect_stderr(err):
            with mock.patch.object(cli, "Client", lambda: Down([])):
                cli.main(["--explain", "d12", "--debug"])
        self.assertEqual(e.exception.code, 3)
        self.assertIn("Traceback (most recent call last)", err.getvalue())
        self.assertEqual(err.getvalue().strip().splitlines()[-1], "brief: could not complete: platform unreachable")

    def test_unexpected_error_exits_1_with_reason_line_and_no_agent_result(self):
        merged = io.StringIO()
        with mock.patch.object(cli, "Client", lambda: sample_client()), \
                mock.patch.object(explain, "run", side_effect=RuntimeError("boom\x1b[31m")), \
                redirect_stdout(merged), redirect_stderr(merged):
            code = cli.main(["--explain", "d12"])
        self.assertEqual(code, 1)
        text = merged.getvalue()
        self.assertNotIn("AGENT_RESULT", text)
        self.assertNotIn("\x1b", text)
        self.assertEqual(text.strip().splitlines()[-1], "brief --explain: unexpected error: RuntimeError: boom[31m")

    def test_explain_from_replay_folder(self):
        tables = {"Pipeline": PIPELINES, "Deal": [deal(1), deal(2)],
                  "Party": [{"id": "pa1", "name": "Party 1"}, {"id": "pa2", "name": "Party 2"}],
                  "Activity": [], "Note": [], "PartyRelationship": [],
                  "UserCompany": [{"id": "uc1", "user_id": "u-1"}]}

        def post(_self, body):
            msg = json.loads(body.decode("utf-8"))
            if msg["method"] == "tools/list":
                res = {"tools": [{"name": f"{e}.list"} for e in tables]}
            else:
                entity = msg["params"]["name"].removesuffix(".list")
                rows_ = list(tables.get(entity, ()))
                res = {"structuredContent": {"data": rows_, "total": len(rows_)}}
            return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res})

        rec_dir = tempfile.mkdtemp()
        os.rmdir(rec_dir)
        env = {"AGENTSWITCH_BASE_URL": "https://example.invalid", "AGENTSWITCH_TOKEN": "tok-1"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(RecordingClient, "_post", post), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--instance", "keystone", "--date", "2026-11-25", "--record", rec_dir,
                                       "--out", tempfile.mkdtemp(), "--snapshots", tempfile.mkdtemp()]), 0)

        out = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=True), redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["--explain", "d02", "--replay", rec_dir])
        self.assertEqual(code, 0)
        self.assertIn("brief date 2026-11-25", out.getvalue())
        self.assertIn("Selection verdict (top 10): SHOWN (rank #1 of 2)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
