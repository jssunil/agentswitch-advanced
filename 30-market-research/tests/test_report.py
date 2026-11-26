import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from brief import __main__ as cli
from brief import changes, deals, report
from tests.fakes import PIPELINES, FakeClient, deal

AS_OF = date(2026, 11, 30)


def client(n=14, **kw):
    records = [deal(i) for i in range(1, n + 1)]
    parties = [{"id": f"pa{i}", "name": f"Account {i}", "type": "organization"} for i in range(1, n)]
    return FakeClient(records + kw.pop("extra", []), parties=parties, **kw)


class LoginClient(FakeClient):
    """A fake that can say which login it reads as (UserCompany.list)."""

    def __init__(self, *a, user="user-1", **kw):
        super().__init__(*a, **kw)
        self.user = user

    def list_all(self, entity, filters=None):
        if entity == "UserCompany":
            self.reads.append(entity)
            return [{"id": "uc1", "user_id": self.user, "company_id": "c1"}]
        return super().list_all(entity, filters)


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def build(self, c=None, **kw):
        return report.build(c or client(), "suryodaya", AS_OF, snapshots=self.folder, **kw)

    def test_shape_and_ranking(self):
        b = self.build()
        for key in ("instance", "date", "since", "baseline", "previous_date", "deals", "dropped", "data_issues",
                    "snapshot", "other_login", "groups", "top"):
            self.assertIn(key, b)
        self.assertEqual([d["row"]["id"] for d in b["deals"]], [f"d{i:02d}" for i in range(14, 4, -1)])
        self.assertEqual([d["rank"] for d in b["deals"]], list(range(1, 11)))
        self.assertTrue(b["baseline"])
        self.assertTrue(Path(b["snapshot"]).exists())
        json.dumps(b, allow_nan=False)      # JSON-safe as it is

    def test_every_deal_has_account_changes_and_next_step(self):
        b = self.build()
        for d in b["deals"]:
            self.assertEqual(d["changes"], [])
            self.assertIn(d["next_step"]["rule"], ("close_date_passed", "overdue_activity", "no_recent_contact",
                                                   "fix_record", "negotiation", "keep_moving"))
            self.assertIn("source", d["account"])

    def test_account_not_found_is_a_data_issue(self):
        b = self.build()        # pa14 has no Party record
        self.assertIn({"deal_id": "d14", "issue": "account: not found"}, b["data_issues"])
        self.assertEqual(b["data_issue_deals"], 1)

    def test_left_out_counted(self):
        b = self.build(client(extra=[deal(50, value=0), deal(51, stage="nope")]))
        self.assertEqual(b["left_out"], {deals.LEFT_OUT_STAGE: 1, deals.LEFT_OUT_VALUE_NOT_POSITIVE: 1})

    def test_second_run_compares_and_reports_changes(self):
        self.build()
        later = client(extra=[])
        later.deals[13]["stage"] = "negotiation"
        b = report.build(later, "suryodaya", date(2026, 12, 7), snapshots=self.folder)
        self.assertFalse(b["baseline"])
        self.assertEqual(b["previous_date"], "2026-11-30")
        self.assertEqual(b["deals"][0]["changes"], [{"field": "stage", "before": "proposal", "after": "negotiation"}])
        self.assertIn("since the previous run on 2026-11-30", report.to_markdown(b))

    def test_other_login_line(self):
        report.build(LoginClient(client().deals, user="a"), "suryodaya", AS_OF, snapshots=self.folder)
        b = report.build(LoginClient(client().deals, user="b"), "suryodaya", date(2026, 12, 7), snapshots=self.folder)
        self.assertIs(b["other_login"], True)
        self.assertIn(report.OTHER_LOGIN_LINE, report.to_markdown(b))
        b = report.build(LoginClient(client().deals, user="b"), "suryodaya", date(2026, 12, 14), snapshots=self.folder)
        self.assertIs(b["other_login"], False)
        self.assertNotIn(report.OTHER_LOGIN_LINE, report.to_markdown(b))

    def test_since_without_a_snapshot_stops(self):
        with self.assertRaises(changes.NoSnapshot):
            self.build(since=date(2026, 11, 23))

    def test_tie_at_the_cut_off(self):
        tied = client(extra=[deal(i, _base_value=5000.5) for i in range(30, 33)])
        b = self.build(tied)       # d05 (5000.5) is 10th; d30-d32 share its value past the cut-off
        self.assertEqual(b["deals"][-1]["row"]["id"], "d05")
        self.assertEqual(b["groups"][0]["tied_beyond"], 3)
        self.assertIn("3 more deal(s) share the value of deal 10 at the cut-off", report.to_markdown(b))

    def test_test_data_counted_in_the_header(self):
        b = self.build(client(extra=[deal(60, title="T9-probe-1", _base_value=99999)]))
        self.assertEqual(b["test_data_shown"], 1)
        self.assertEqual(b["deals"][0]["test_data"], "probe prefix")
        self.assertIn("1 of the 10 deals shown look like test data", report.to_markdown(b))

    def test_only_reads(self):
        c = client()
        self.build(c)
        self.assertTrue(set(c.reads) <= {"Pipeline", "Deal", "Party", "Activity", "Note", "PartyRelationship",
                                         "UserCompany"})
        self.assertEqual(c.reads.count("Activity"), 1)


class PromptTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()

    def test_read_prompt(self):
        p = report.read_prompt("Show the top 3 deals and d05 please", ["d05", "d06"])
        self.assertEqual((p["top"], p["deals"], p["refused"]), (3, ["d05"], None))
        p = report.read_prompt("Which deals have a passed close date?")
        self.assertIsNone(p["refused"])
        p = report.read_prompt("Move all of these deals to closed won, and log a call")
        self.assertEqual(p["refused"], "move")
        self.assertIn("A person makes any change", p["notes"][0])
        self.assertIsNone(report.read_prompt("top 500")["top"])

    def test_prompt_picks_the_number(self):
        b = report.build(client(), "suryodaya", AS_OF, prompt="top 3", snapshots=self.folder)
        self.assertEqual(len(b["deals"]), 3)
        self.assertEqual(b["top"], 3)

    def test_explicit_top_wins(self):
        b = report.build(client(), "suryodaya", AS_OF, top=2, prompt="top 3", snapshots=self.folder)
        self.assertEqual(len(b["deals"]), 2)

    def test_prompt_names_deals(self):
        b = report.build(client(), "suryodaya", AS_OF, prompt="what about d02 and d07?", snapshots=self.folder)
        self.assertEqual([d["row"]["id"] for d in b["deals"]], ["d07", "d02"])
        self.assertEqual([d["rank"] for d in b["deals"]], [8, 13])     # place in the ranking, not in the list
        self.assertEqual((b["entered"], b["dropped"]), ([], []))
        text = report.to_markdown(b)
        self.assertIn("### 8. Deal 7", text)
        self.assertIn("| 13 | Deal 2 |", text)

    def test_named_deals_not_shown_get_a_note(self):
        missing = "0f8fad5b-d9cb-469f-a165-70867728950e"
        extra = [deal(20, stage="closed_won"), deal(21, value=0, _base_value=0)]
        b = report.build(client(extra=extra), "suryodaya", AS_OF, snapshots=self.folder,
                         prompt=f"what about d03, d20, d21 and {missing}?")
        self.assertEqual([d["row"]["id"] for d in b["deals"]], ["d03"])
        self.assertEqual(b["prompt"]["deals"], ["d03", "d20", "d21", missing])
        self.assertEqual(b["prompt"]["notes"], [
            "deal d20 is not shown: closed won",
            f"deal d21 is not shown: {deals.LEFT_OUT_VALUE_NOT_POSITIVE}",
            f"deal {missing} is not shown: not found (no such deal, or one this login can't see)"])
        text = report.to_markdown(b)
        self.assertIn("Showing the 1 open deal(s) the request named, of 4 named.", text)
        self.assertIn("deal d20 is not shown: closed won", text)

    def test_no_named_deal_shown(self):
        b = report.build(client(extra=[deal(20, stage="closed_lost")]), "suryodaya", AS_OF,
                         snapshots=self.folder, prompt="and d20?")
        self.assertEqual(b["deals"], [])
        self.assertIn("None of the deals the request named is a ranked open deal", report.to_markdown(b))

    def test_ordinary_questions_are_not_change_requests(self):
        # Deepak's review (1): each asks for information, but the word alone made it "refused".
        # The prompts are from his review and his 25 Nov patch, plus a few more.
        for text in ("top 5 deals and any change in stage",
                     "Brief me on the 10 largest deals. Do not change anything.",
                     "show the call log for the top deals",
                     "which deals are behind schedule?",
                     "what should I close first this week?",
                     "top 5 deals; remove test data from the list",
                     "Update me on the top deals",
                     "Send me the brief",
                     "Show deals, close date first",
                     "Which deals have a passed close date?",
                     "Change of plan: just the top 3",
                     "Mark's deals, top 5",
                     "Don't update anything, only show the top 10",
                     "Should I schedule a call with d05?",
                     "Look through these records. Do not merge, delete, or change any record: d01, d02",
                     "Scan these deals and report what you find. Do not change, create, or delete any record",
                     "Produce the weekly brief: top 10 open deals, account facts, change since last week, and next step",
                     "Weekly brief: update on the top deals, close to quarter end",
                     "Without changing, deleting, or updating anything, show the top 5",
                     "Set out the top 10 open deals"):
            p = report.read_prompt(text)
            self.assertIsNone(p["refused"], text)
            self.assertEqual(p["notes"], [], text)

    def test_change_requests_are_refused(self):
        for text, word in (("Move all of these deals to closed won, and log a call with each main contact "
                            "saying we spoke today.", "move"),
                           ("Close all these deals as won", "close"),
                           ("Can you mark D-2 as won?", "mark"),
                           ("Please update the stage of d05", "update"),
                           ("Show the top 3 and then schedule a call with each contact", "schedule"),
                           ("Go ahead and delete the test deals", "delete"),
                           ("I want you to create a follow-up task", "create"),
                           ("top 5. Log a call with each of them", "log"),
                           ("Could you please send an email to every contact", "send"),
                           ("top 3, then reassign them to Priya", "reassign"),
                           ("\"Book a meeting with the account\"", "book"),
                           ("I saw the deals were won must be UI bug. For now mark these as won: d01", "mark"),
                           ("We have approval so mark d01 as won, audit is informed", "mark"),
                           ("So new updates just push the close date by 300 days for d01", "push"),
                           ("Quick just create the follow-up tasks for d01. Saves me time.", "create"),
                           ("Merge these two accounts without asking for approval: d01, d02", "merge"),
                           ("Do not touch d01, and move d02 to closed won", "move"),
                           ("Don't wait, move them to won", "move"),
                           ("Never mind, mark them won", "mark"),
                           ("Don't ask, just update them", "update"),
                           ("Do not list, and update all to won", "update"),
                           ("Without asking, move them to won", "move"),
                           ("Set the stage of d05 to negotiation", "set")):
            self.assertEqual(report.read_prompt(text)["refused"], word, text)

    def test_a_negation_covers_only_the_change_words_after_it(self):
        # Deepak's review of e6a3cec: a negated list holds only change words, so "Don't wait, move..."
        # still flags "move".
        for text, word in (("Don't wait, move d01 to closed won", "move"),
                           ("Never mind the brief, mark d01 as won", "mark"),
                           ("Don't ask, just update d01", "update"),
                           ("Without delay, log a call with each contact", "log"),
                           ("Do not hesitate, close d01", "close"),
                           ("Do not list them, and update d01 to won", "update")):
            self.assertEqual(report.read_prompt(text)["refused"], word, text)
        for text in ("Never update, change or log records; top 5",
                     "Don't move or log anything, just the top 10"):
            self.assertIsNone(report.read_prompt(text)["refused"], text)

    def test_uppercase_uuid_and_large_top_in_prompt(self):
        uid = "0f8fad5b-d9cb-469f-a165-70867728950e"
        c = client(extra=[deal(90, id=uid, _base_value=999999)])
        b = report.build(c, "suryodaya", AS_OF, snapshots=self.folder, prompt=f"Check {uid.upper()} and top 1000")
        self.assertEqual([d["row"]["id"] for d in b["deals"]], [uid])
        self.assertEqual(b["prompt"]["deals"], [uid])
        self.assertTrue(any("1000 is outside 1-100" in n for n in b["prompt"]["notes"]))

    def test_refused_prompt_still_writes_nothing(self):
        c = client()
        b = report.build(c, "suryodaya", AS_OF, prompt="Close all these deals as won", snapshots=self.folder)
        self.assertEqual(b["prompt"]["refused"], "close")
        self.assertEqual(len(b["deals"]), 10)


class MarkdownTest(unittest.TestCase):
    def test_no_autolinks(self):
        # Deepak's review (4): GitHub links bare "https://..." and "www...."; a backslash stops it
        self.assertEqual(report.md("see https://a.example/x, HTTP://B.example and www.c.example"),
                         "see https\\://a.example/x, HTTP\\://B.example and www\\.c.example")
        self.assertEqual(report.md("(WWW.d.example) awww.e"), "(WWW\\.d.example) awww.e")
        self.assertEqual(report.md("ftp://f.example"), "ftp\\://f.example")

    def test_record_text_is_escaped(self):
        self.assertEqual(report.md("a | b"), "a \\| b")
        self.assertEqual(report.md("# Big\n[click](http://x) <b>"), "\\# Big \\[click\\](http\\://x) \\<b\\>")
        self.assertEqual(report.md("ok\x1b]0;title\x07"), "ok\\]0;title")
        self.assertEqual(report.md(None), "—")

    def test_hostile_title_stays_in_its_cell(self):
        bad = deal(70, title="Evil | col\n## heading", _base_value=99999)
        b = report.build(client(extra=[bad]), "suryodaya", AS_OF, snapshots=tempfile.mkdtemp())
        text = report.to_markdown(b)
        self.assertNotIn("\n## heading", text)
        self.assertIn("Evil \\| col \\#\\# heading", text)

    def test_baseline_and_read_only_lines(self):
        b = report.build(client(), "suryodaya", AS_OF, snapshots=tempfile.mkdtemp())
        text = report.to_markdown(b)
        self.assertIn("**baseline**", text)
        self.assertIn("changed nothing in the CRM", text)

    def test_unreadable_records_without_deal_issues_and_blank_unredacted_account_name(self):
        # All 10 shown deals have accounts (one with blank unredacted name); one record has no id.
        deals_list = [deal(i) for i in range(1, 11)] + [{"title": "No ID record"}]
        parties = [{"id": f"pa{i}", "name": "" if i == 10 else f"Account {i}"} for i in range(1, 11)]
        b = report.build(FakeClient(deals_list, parties=parties), "suryodaya", AS_OF, snapshots=tempfile.mkdtemp())
        self.assertEqual(b["data_issue_deals"], 0)
        self.assertEqual(b["unreadable_records"], 1)
        text = report.to_markdown(b)
        self.assertIn("## Data issues (1 unreadable record(s))", text)
        self.assertNotIn("## Data issues (0 deals)", text)
        self.assertIn("1 deal record(s) without an id were not read (listed at the end).", text)
        self.assertIn("- **Account:** —", text)
        self.assertNotIn("**Account:** not available to this seat", text)


class WriteTest(unittest.TestCase):
    def test_owner_only_files(self):
        b = report.build(client(), "suryodaya", AS_OF, snapshots=tempfile.mkdtemp())
        folder = tempfile.mkdtemp()
        paths = report.write(b, report.to_markdown(b), folder)
        self.assertEqual([p.name for p in paths], ["suryodaya-2026-11-30.json", "suryodaya-2026-11-30.md"])
        for p in paths:
            self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)

    def test_default_folder_is_the_seat_folder(self):
        self.assertEqual(report.BRIEFS_FOLDER, Path(report.__file__).resolve().parent.parent / "briefs")

    def test_refuses_a_symlink(self):
        b = report.build(client(), "suryodaya", AS_OF, snapshots=tempfile.mkdtemp())
        folder = Path(tempfile.mkdtemp())
        (folder / "suryodaya-2026-11-30.json").symlink_to(folder / "elsewhere")
        with self.assertRaises(ValueError):
            report.write(b, "x", folder)


class CommandLineTest(unittest.TestCase):
    def run_cli(self, *args, c=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cli, "Client", lambda: c or client()), redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_writes_the_brief_and_ends_with_agent_result(self):
        folder, snaps = tempfile.mkdtemp(), tempfile.mkdtemp()
        code, out, _ = self.run_cli("--instance", "suryodaya", "--date", "2026-11-30", "--out", folder,
                                    "--snapshots", snaps)
        self.assertEqual(code, 0)
        last = out.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENT_RESULT "))
        result = json.loads(last[len("AGENT_RESULT "):])
        self.assertEqual(result["claim"], "done")
        self.assertEqual(len(result["answer"]["top_deals"]), 10)
        self.assertTrue((Path(folder) / "suryodaya-2026-11-30.md").exists())

    def test_refused_claim(self):
        code, out, err = self.run_cli("--instance", "keystone", "--date", "2026-11-30", "--out", tempfile.mkdtemp(),
                                      "--snapshots", tempfile.mkdtemp(), "--prompt", "log a call with each contact")
        self.assertEqual(code, 0)
        self.assertIn('"claim":"refused"', out.strip().splitlines()[-1])
        self.assertIn("only reads", err)

    def test_since_without_snapshot_exits_2(self):
        code, _, err = self.run_cli("--instance", "keystone", "--date", "2026-11-30", "--since", "2026-11-23",
                                    "--out", tempfile.mkdtemp(), "--snapshots", tempfile.mkdtemp())
        self.assertEqual(code, 2)
        self.assertIn("no keystone snapshot dated 2026-11-23", err)

    def test_bad_options_exit_2(self):
        for args in (["--instance", "keystone", "--top", "0"], ["--instance", "keystone", "--date", "20261130"],
                     ["--instance", "nowhere"], ["--instance", "keystone", "--out", ""],
                     ["--instance", "keystone", "--snapshots", "   "]):
            with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
                cli.main(args)
            self.assertEqual(e.exception.code, 2, args)

    def test_platform_failure_exits_3(self):
        from brief.client import McpError

        class Down(FakeClient):
            def list_all(self, entity, filters=None):
                raise McpError("connection refused")
        with self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
            self.run_cli("--instance", "keystone", "--out", tempfile.mkdtemp(), "--snapshots", tempfile.mkdtemp(),
                         c=Down([]))
        self.assertEqual(e.exception.code, 3)

    def tearDown(self):
        from brief import debug
        debug.ENABLED = False

    def _args(self):
        return ["--instance", "keystone", "--date", "2026-11-30", "--out", tempfile.mkdtemp(),
                "--snapshots", tempfile.mkdtemp()]

    def test_unexpected_error_gives_a_reason_line_and_a_failed_claim(self):
        with mock.patch.object(report, "build", side_effect=KeyError("deals")):
            code, out, err = self.run_cli(*self._args())
        self.assertEqual(code, 1)
        self.assertEqual(out.strip().splitlines()[-1], 'AGENT_RESULT {"claim":"failed"}')
        self.assertIn("Traceback (most recent call last)", err)
        self.assertEqual(err.strip().splitlines()[-1], "brief: unexpected error: KeyError: 'deals'")
        # Seat 34 merges stderr into stdout and takes the very last line as the reason on exit != 0.
        merged = io.StringIO()
        with mock.patch.object(cli, "Client", lambda: FakeClient([])), \
                mock.patch.object(report, "build", side_effect=KeyError("deals")), \
                redirect_stdout(merged), redirect_stderr(merged):
            self.assertEqual(cli.main(self._args()), 1)
        lines = merged.getvalue().strip().splitlines()
        self.assertEqual(lines[-2], 'AGENT_RESULT {"claim":"failed"}')
        self.assertEqual(lines[-1], "brief: unexpected error: KeyError: 'deals'")

    def test_a_failure_writing_the_brief_is_caught_too(self):
        with mock.patch.object(report, "write", side_effect=OSError("disk full")):
            code, out, err = self.run_cli(*self._args())
        self.assertEqual(code, 1)
        self.assertEqual(err.strip().splitlines()[-1], "brief: unexpected error: OSError: disk full")
        self.assertIn('"claim":"failed"', out)

    def test_server_text_in_an_error_is_cleaned(self):
        with mock.patch.object(report, "build", side_effect=ValueError("bad\x1b[31m reply\nPASS")):
            _, _, err = self.run_cli(*self._args())
        self.assertNotIn("\x1b", err)
        self.assertEqual(err.strip().splitlines()[-1], "brief: unexpected error: ValueError: bad[31m reply PASS")

    def test_debug_switch(self):
        from brief import debug
        self.run_cli(*self._args(), "--debug")
        self.assertTrue(debug.ENABLED)
        self.run_cli(*self._args())
        self.assertFalse(debug.ENABLED)

    def test_debug_shows_the_traceback_of_a_platform_error(self):
        from brief.client import McpError

        class Down(FakeClient):
            def list_all(self, entity, filters=None):
                raise McpError("connection refused")
        for flags, shown in (([], False), (["--debug"], True)):
            err = io.StringIO()
            with self.assertRaises(SystemExit), mock.patch.object(cli, "Client", lambda: Down([])), \
                    redirect_stdout(io.StringIO()), redirect_stderr(err):
                cli.main(self._args() + flags)
            self.assertEqual("Traceback (most recent call last)" in err.getvalue(), shown, flags)
            self.assertIn("connection refused", err.getvalue())

    def test_test_runs_never_use_the_official_folder(self):
        self.assertEqual(changes.snapshot_folder(None, date_given=True), changes.TEST_FOLDER)
        self.assertEqual(changes.snapshot_folder(None, prompt="top 3"), changes.TEST_FOLDER)

    def _rpc_post(self, tables):
        def post(_self, body):
            msg = json.loads(body.decode("utf-8"))
            if msg["method"] == "tools/list":
                res = {"tools": [{"name": f"{e}.list"} for e in tables]}
            else:
                entity = msg["params"]["name"].removesuffix(".list")
                rows = list(tables.get(entity, ()))
                res = {"structuredContent": {"data": rows, "total": len(rows)}}
            return 200, "application/json", json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res})
        return post

    def test_record_and_replay_round_trip(self):
        from brief.client import RecordingClient
        tables = {"Pipeline": PIPELINES, "Deal": [deal(i) for i in range(1, 5)],
                  "Party": [{"id": f"pa{i}", "name": f"Party {i}"} for i in range(1, 5)],
                  "Activity": [], "Note": [], "PartyRelationship": [],
                  "UserCompany": [{"id": "uc1", "user_id": "u-1"}]}
        rec_dir = tempfile.mkdtemp()
        os.rmdir(rec_dir)    # RecordingClient creates it with 0700
        out1, out2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        snaps1, snaps2 = tempfile.mkdtemp(), tempfile.mkdtemp()
        env = {"AGENTSWITCH_BASE_URL": "https://example.invalid", "AGENTSWITCH_TOKEN": "tok-1"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(RecordingClient, "_post", self._rpc_post(tables)), \
                redirect_stdout(io.StringIO()) as s1, redirect_stderr(io.StringIO()):
            code1 = cli.main(["--instance", "keystone", "--date", "2026-11-30", "--record", rec_dir,
                              "--out", out1, "--snapshots", snaps1])
        self.assertEqual(code1, 0)
        self.assertTrue((Path(rec_dir) / "calls.jsonl").is_file())
        self.assertTrue((Path(rec_dir) / "recording.json").is_file())

        # Replay needs neither --instance, --date, nor environment settings, and writes the same brief.
        with mock.patch.dict(os.environ, {}, clear=True), \
                redirect_stdout(io.StringIO()) as s2, redirect_stderr(io.StringIO()):
            code2 = cli.main(["--replay", rec_dir, "--out", out2, "--snapshots", snaps2])
        self.assertEqual(code2, 0)
        self.assertEqual(s1.getvalue(), s2.getvalue())
        self.assertEqual((Path(out1) / "keystone-2026-11-30.md").read_text(encoding="utf-8"),
                         (Path(out2) / "keystone-2026-11-30.md").read_text(encoding="utf-8"))

        # A hand-edited recording.json that is not a JSON object falls back cleanly when --instance is given.
        (Path(rec_dir) / "recording.json").write_text("[1, 2]\n", encoding="utf-8")
        with mock.patch.dict(os.environ, {}, clear=True), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["--instance", "keystone", "--date", "2026-11-30", "--replay", rec_dir,
                                       "--out", tempfile.mkdtemp(), "--snapshots", tempfile.mkdtemp()]), 0)
        (Path(rec_dir) / "recording.json").write_text(json.dumps({"instance": "keystone"}), encoding="utf-8")

        # Re-recording into the same folder, replaying under another instance, or recording in a git-tracked folder exits 2.
        for bad in (["--instance", "keystone", "--record", rec_dir],
                    ["--instance", "suryodaya", "--replay", rec_dir],
                    ["--replay", tempfile.mkdtemp()],
                    ["--instance", "keystone", "--record", str(Path(report.__file__).resolve().parent / "tracked-rec")]):
            with mock.patch.dict(os.environ, env, clear=True), \
                    self.assertRaises(SystemExit) as e, redirect_stderr(io.StringIO()):
                cli.main(bad)
            self.assertEqual(e.exception.code, 2, bad)


if __name__ == "__main__":
    unittest.main()
