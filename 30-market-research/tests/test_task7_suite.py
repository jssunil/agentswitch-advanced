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
    act(12, party_id="pa2", deal_id="d05", due_date="2026-11-28"),       # another deal's call on pa2: not d02's
    act(13, party_id="pa4", due_date="2026-11-12", type=" Call "),       # d04: type matched case-insensitively
    act(14, deal_id="d06", due_date="2026-11-05", done=False, cancelled=True),  # d06: only a cancelled one
    act(15, party_id="pa6", due_date="2026-11-18", type="meeting"),      # so d06 falls back to its account
    act(16, party_id="pa7", due_date="2026-11-20", type="lunch"),        # d07: lunch is not a contact
    act(17, party_id="pa8", due_date="2026-11-22", type="email"),        # d08: two contacts on one date,
    act(18, party_id="pa8", due_date="2026-11-22", type="meeting"),      # the higher id (a18) wins
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

    def test_fallback_ignores_other_deals_activities(self):
        # edge case 16: a12 belongs to d05, so it neither counts for d02 nor stops d02's fallback
        f = self.facts(2)
        self.assertNotEqual(f["last_contact"], {"date": "2026-11-28", "type": "call"})
        self.assertEqual(self.facts(5)["source"], "deal")
        self.assertEqual(self.facts(5)["last_contact"], {"date": "2026-11-28", "type": "call"})

    def test_contact_type_case_insensitive(self):
        self.assertEqual(self.facts(4)["last_contact"], {"date": "2026-11-12", "type": "call"})

    def test_cancelled_ignored_when_choosing_source(self):
        f = self.facts(6)
        self.assertEqual(f["source"], "party")
        self.assertEqual(f["last_contact"], {"date": "2026-11-18", "type": "meeting"})
        self.assertEqual(f["open_activities"], [])

    def test_lunch_is_not_a_contact(self):
        f = self.facts(7)
        self.assertEqual((f["source"], f["last_contact"]), ("party", None))

    def test_same_date_tie_goes_to_highest_id(self):
        self.assertEqual(self.facts(8)["last_contact"], {"date": "2026-11-22", "type": "meeting"})

    def test_compare_accepts_type_in_any_case(self):
        want = self.facts(1)
        got = {"name": want["name"], "redacted": [], "source": "deal",
               "last_contact": {"date": "2026-11-25", "type": "Call", "subject": "x"},
               "open_activities": [dict(a) for a in want["open_activities"]]}
        self.assertNotIn("last_contact", tasks_brief._compare_facts(want, got))

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
        self.assertIn("deals=10; with_activity=6; wrong_deals=0", r["evidence"])

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



class BrokenModules(unittest.TestCase):
    """A module that exists but can't be imported is a failure, never "not built yet".
    Imports are simulated, so these tests hold before and after Sunil's modules exist."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.ctx = {"instance": "keystone", "as_of": AS_OF, "workdir": self._tmp.name}

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def imports(missing=(), broken=()):
        """importlib.import_module where `missing` modules don't exist and `broken` ones fail inside."""
        import importlib
        real = importlib.import_module

        def fake(name, *a):
            short = name.rsplit(".", 1)[-1]
            if short in missing:
                raise ModuleNotFoundError(f"No module named {name!r}", name=name)
            if short in broken:
                raise ModuleNotFoundError("No module named 'brief.typo_inside'", name="brief.typo_inside")
            return real(name, *a)
        return mock.patch("importlib.import_module", fake)

    def test_helper(self):
        import brief
        with self.imports(missing={"report"}):
            self.assertIsNone(brief.optional_module("report"))
        with self.imports(broken={"report"}), self.assertRaises(ModuleNotFoundError):
            brief.optional_module("report")
        self.assertIs(brief.optional_module("rows"), __import__("brief.rows").rows)

    def test_broken_report_fails_tasks_instead_of_component_mode(self):
        with self.imports(broken={"report"}):
            for task in (tasks_brief.what_changed, tasks_brief.no_invented_change, tasks_brief.next_step_rule,
                         tasks_brief.refuse_update, tasks_brief.brief_read_only, tasks_brief.account_facts):
                r = task(client(), self.ctx)
                self.assertEqual((r["passed"], r["score"]), (False, 0.0), task.__name__)
                self.assertIn("infrastructure: ModuleNotFoundError", r["evidence"], task.__name__)
                self.assertIn("typo_inside", r["evidence"], task.__name__)

    def test_missing_report_still_means_component_mode(self):
        with self.imports(missing={"report"}):
            r = tasks_brief.brief_read_only(client(), self.ctx)
        self.assertEqual((r["passed"], r["score"]), (True, 0.5), r["evidence"])

    def test_broken_accounts_fails_task_7(self):
        with self.imports(missing={"report"}, broken={"accounts"}):
            r = tasks_brief.account_facts(client(), self.ctx)
        self.assertEqual((r["passed"], r["score"]), (False, 0.0))
        self.assertIn("typo_inside", r["evidence"])

    def test_broken_tasks_deals_is_named_in_the_suite(self):
        with self.imports(broken={"tasks_deals"}):
            out = suite.run_suite(client(), "keystone", AS_OF, only={"top10_ids"})
        self.assertIn("tasks_deals could not be imported: ModuleNotFoundError", out["tasks"][0]["evidence"])
        self.assertIn("typo_inside", out["tasks"][0]["evidence"])

    def test_missing_tasks_deals_is_not_built_yet(self):
        with self.imports(missing={"tasks_deals"}):
            out = suite.run_suite(client(), "keystone", AS_OF, only={"top10_ids"})
        self.assertEqual(out["tasks"][0]["evidence"], "infrastructure: tasks_deals not built yet")


if __name__ == "__main__":
    unittest.main()


if __name__ == "__main__":
    unittest.main()
