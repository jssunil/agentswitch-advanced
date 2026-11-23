"""Task 7 (account facts) and the suite runner, on made-up data."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest import mock

from brief import nextstep, suite, tasks_brief
from brief.rows import from_record, stage_labels, to_dict

from tests.fakes import PIPELINES, FakeClient, deal

AS_OF = date(2026, 11, 30)
LABELS = stage_labels(PIPELINES)


def act(i, **over):
    a = {"id": f"a{i:02d}", "subject": f"Activity {i}", "type": "call", "due_date": "2026-11-20",
         "done": True, "cancelled": False, "deal_id": None, "party_id": None}
    a.update(over)
    return a


PARTIES = [{"id": f"pa{i}", "name": f"Account {i}", "_redacted_fields": ["pan"]} for i in range(1, 20)]
PARTIES[2]["_redacted_fields"] = ["name"]     # pa3: name hidden from this seat
ACTIVITIES = [
    act(1, deal_id="d01", due_date="2026-11-10", type="meeting"),        # d01: deal-linked contact
    act(2, deal_id="d01", due_date="2026-11-25", type="call"),           # later contact wins
    act(3, deal_id="d01", due_date="2026-12-05", type="call"),           # after as_of: not a contact yet
    act(4, deal_id="d01", due_date="2026-11-28", type="task"),           # a task is not a contact
    act(5, deal_id="d01", due_date="2026-11-29", type="email", cancelled=True),
    act(6, deal_id="d01", due_date="2026-11-01", done=False),            # open, overdue
    act(7, deal_id="d01", due_date="2026-12-10", done=False),            # open, not overdue
    act(8, deal_id="d01", due_date=None, done=False),                    # open, no date
    act(9, party_id="pa1", due_date="2026-11-29"),                       # account-level: ignored, d01 has its own
    act(10, party_id="pa2", due_date="2026-10-01", type="email"),        # d02: falls back to its account
    act(11, party_id="pa2", due_date="2026-11-15", done=False),
]


def client():
    return FakeClient([deal(i) for i in range(1, 13)], activities=ACTIVITIES, parties=PARTIES)


class ExpectedFacts(unittest.TestCase):
    """The recount itself, against hand-worked answers."""
    parties = {p["id"]: p for p in PARTIES}

    def facts(self, n):
        return tasks_brief.expected_facts(from_record(deal(n), LABELS), self.parties, ACTIVITIES, AS_OF)

    def test_deal_linked(self):
        f = self.facts(1)
        self.assertEqual(f["source"], "deal")
        self.assertEqual(f["name"], "Account 1")
        self.assertEqual(f["last_contact"], {"date": "2026-11-25", "type": "call"})
        self.assertEqual(f["open_activities"], [
            {"id": "a06", "due_date": "2026-11-01", "overdue": True},
            {"id": "a07", "due_date": "2026-12-10", "overdue": False},
            {"id": "a08", "due_date": None, "overdue": False}])

    def test_account_fallback(self):
        f = self.facts(2)
        self.assertEqual(f["source"], "party")
        self.assertEqual(f["last_contact"], {"date": "2026-10-01", "type": "email"})
        self.assertEqual(f["open_activities"], [{"id": "a11", "due_date": "2026-11-15", "overdue": True}])

    def test_nothing_and_redacted_name(self):
        f = self.facts(3)
        self.assertEqual((f["source"], f["last_contact"], f["open_activities"]), (None, None, []))
        self.assertIsNone(f["name"])
        self.assertTrue(f["name_redacted"])

    def test_missing_party(self):
        f = tasks_brief.expected_facts(from_record(deal(1, party_id="nope"), LABELS), self.parties, [], AS_OF)
        self.assertEqual((f["name"], f["name_redacted"]), (None, False))


def good_facts(client_, row, as_of):
    """What a correct accounts.facts() returns (built from the same rules, shaped as PLAN.md says)."""
    parties = {p["id"]: p for p in client_.list_all("Party")}
    want = tasks_brief.expected_facts(row, parties, client_.list_all("Activity"), as_of)
    redacted = ["name"] if want["name_redacted"] else []
    return {"party_id": row.party_id, "name": want["name"], "source": want["source"],
            "last_contact": None if want["last_contact"] is None else {**want["last_contact"], "subject": "x"},
            "open_activities": [{**a, "subject": "y"} for a in want["open_activities"]],
            "redacted": redacted, "pinned_note": None, "contacts": 0}


def build_with(facts_fn):
    def build(client_, instance, as_of, top=10, since=None, prompt=None, snapshots=None):
        rows = [from_record(r, stage_labels(client_.list_all("Pipeline"))) for r in client_.list_all("Deal")]
        shown = sorted(rows, key=lambda r: r.id)[:top]
        return {"baseline": True, "deals": [{"rank": i, "row": to_dict(r), "account": facts_fn(client_, r, as_of),
                                             "changes": [], "next_step": nextstep.suggest(r, None, as_of)}
                                            for i, r in enumerate(shown, 1)]}
    return build


class AccountFacts(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ctx = {"instance": "keystone", "as_of": AS_OF, "workdir": self._tmp.name}

    def tearDown(self):
        self._tmp.cleanup()

    def run_task(self, facts_fn):
        return tasks_brief.account_facts(client(), self.ctx, build=build_with(facts_fn))

    def test_correct_facts_pass(self):
        r = self.run_task(good_facts)
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("deals=10; with_activity=2; wrong_deals=0", r["evidence"])

    def test_each_kind_of_mistake_is_caught(self):
        def wrong(change):
            def facts(c, row, as_of):
                f = good_facts(c, row, as_of)
                if row.id in ("d01", "d02", "d03"):
                    change(f)
                return f
            return facts
        cases = {
            "name": lambda f: f.update(name="Someone else") if f["name"] else f.update(name="Shown anyway"),
            "source": lambda f: f.update(source="party" if f["source"] != "party" else "deal"),
            "last_contact": lambda f: f.update(last_contact={"date": "2026-01-01", "type": "call"}),
            "open_activities": lambda f: f.update(open_activities=f["open_activities"][1:] + [{"id": "zz"}]),
        }
        for fact, change in cases.items():
            r = self.run_task(wrong(change))
            self.assertFalse(r["passed"], fact)
            self.assertIn("wrong_deals=3", r["evidence"], fact)
            self.assertEqual(r["score"], 0.7, fact)

    def test_redacted_name_must_not_be_shown(self):
        def facts(c, row, as_of):
            f = good_facts(c, row, as_of)
            if row.id == "d03":
                f.update(name="Leaked", redacted=[])
            return f
        r = self.run_task(facts)
        self.assertIn("'redacted name shown': 1", r["evidence"])

    def test_overdue_flag_must_match(self):
        def facts(c, row, as_of):
            f = good_facts(c, row, as_of)
            for a in f["open_activities"]:
                a["overdue"] = False
            return f
        self.assertIn("'open_activities': 2", self.run_task(facts)["evidence"])

    def test_missing_account_block(self):
        r = self.run_task(lambda c, row, as_of: None)
        self.assertIn("'no account facts': 10", r["evidence"])

    def test_component_mode_uses_accounts_facts(self):
        fake = mock.MagicMock(facts=good_facts)
        with mock.patch.dict("sys.modules", {"brief.accounts": fake}), mock.patch.object(tasks_brief, "_build", lambda: None):
            r = tasks_brief.account_facts(client(), self.ctx)
        self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])

    def test_nothing_built_is_infrastructure(self):
        with mock.patch.dict("sys.modules", {"brief.accounts": None}), mock.patch.object(tasks_brief, "_build", lambda: None):
            r = tasks_brief.account_facts(client(), self.ctx)
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("infrastructure: neither brief.report nor brief.accounts", r["evidence"])


class Suite(unittest.TestCase):
    def test_every_planned_task_is_reported_in_order(self):
        out = suite.run_suite(client(), "keystone", AS_OF)
        self.assertEqual([t["id"] for t in out["tasks"]], [f"keystone:{t}" for t in suite.PLANNED])
        missing = [t for t in out["tasks"][:6]]
        for t in missing:
            self.assertEqual((t["passed"], t["score"]), (False, 0.0))
            self.assertEqual(t["evidence"], "infrastructure: tasks_deals not built yet")
        for t in out["tasks"]:
            self.assertEqual(set(t), {"id", "title", "passed", "score", "evidence"})
        self.assertRegex(out["summary"], r"^keystone: \d+/12 tasks passed$")

    def test_only(self):
        out = suite.run_suite(client(), "keystone", AS_OF, only={"next_step_rule"})
        self.assertEqual([t["id"] for t in out["tasks"]], ["keystone:next_step_rule"])

    def test_platform_error_reports_every_task(self):
        out = suite.run_suite(None, "suryodaya", AS_OF, platform_error="AGENTSWITCH_TOKEN is not set")
        self.assertEqual(len(out["tasks"]), 12)
        self.assertTrue(all(t["evidence"].startswith("infrastructure: the platform could not be reached") for t in out["tasks"]))

    def test_bad_task_results_become_infrastructure(self):
        for bad in (None, {"passed": "yes", "score": 1, "evidence": ""}, {"passed": True, "score": 2.0, "evidence": ""},
                    {"passed": True, "score": float("nan"), "evidence": ""}, {"passed": True, "score": 0.0, "evidence": ""}):
            self.assertFalse(suite._checked(bad)["passed"], bad)
        def boom(c, ctx):
            raise RuntimeError("x")
        with mock.patch.object(suite, "all_tasks", lambda: [("t", "T", boom, None)]):
            out = suite.run_suite(client(), "keystone", AS_OF)
        self.assertEqual(out["tasks"][0]["evidence"], "infrastructure: RuntimeError: x")

    def test_main_writes_results_and_exits_3_without_a_platform(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict("os.environ", {}, clear=True), redirect_stdout(io.StringIO()):
            code = suite.main(["--instance", "keystone", "--date", "2026-11-30", "--out", f"{d}/results.json"])
            payload = json.loads(Path(d, "results.json").read_text())
        self.assertEqual(code, 3)
        self.assertEqual(len(payload["tasks"]), 12)
        self.assertEqual(payload["summary"], "keystone: 0/12 tasks passed")

    def test_unknown_task_id_is_a_usage_error(self):
        with self.assertRaises(SystemExit) as e, mock.patch("sys.stderr", io.StringIO()):
            suite.main(["--instance", "keystone", "--only", "nope"])
        self.assertEqual(e.exception.code, 2)

    def test_today_is_ist(self):
        self.assertIsInstance(suite.today_ist(), date)


if __name__ == "__main__":
    unittest.main()
