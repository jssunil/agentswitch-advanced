"""nextstep.py: the first matching rule wins, and every step carries its reason."""
import unittest
from unittest import mock
from datetime import date

from brief import nextstep
from brief.rows import from_record, stage_labels

from tests.fakes import PIPELINES, deal

LABELS = stage_labels(PIPELINES)
TODAY = date(2026, 11, 23)
RECENT = {"last_contact": {"date": "2026-11-20", "type": "call", "subject": "x"}, "open_activities": []}


def row(**over):
    return from_record(deal(1, **over), LABELS)


class Rules(unittest.TestCase):
    def rule(self, r, facts=RECENT):
        return nextstep.suggest(r, facts, TODAY)

    def test_close_date_passed_comes_first_with_date_and_days(self):
        out = self.rule(row(expected_close_date="2026-10-31", probability=0.0), {"last_contact": None})
        self.assertEqual(out["rule"], "close_date_passed")
        self.assertIn("2026-10-31", out["reason"])
        self.assertIn("23 days", out["reason"])
        self.assertIn("Proposal", out["reason"])

    def test_close_date_today_has_not_passed(self):
        self.assertNotEqual(self.rule(row(expected_close_date="2026-11-23"))["rule"], "close_date_passed")

    def test_one_day_is_singular(self):
        self.assertIn("1 day ago", self.rule(row(expected_close_date="2026-11-22"))["reason"])

    def test_oldest_overdue_activity(self):
        facts = {**RECENT, "open_activities": [
            {"id": "a2", "subject": "Send revised quote", "due_date": "2026-11-20"},
            {"id": "a1", "subject": "Site\x1b visit\nplan", "due_date": "2026-11-10"},
            {"id": "a3", "subject": "Later", "due_date": "2026-12-01"},
            {"id": "a4", "subject": "No date", "due_date": None}, "junk"]}
        out = self.rule(row(), facts)
        self.assertEqual(out["rule"], "overdue_activity")
        # our wording is fixed; the record's subject comes back separately, cleaned
        self.assertEqual(out["step"], "Complete the overdue activity")
        self.assertEqual(out["activity"], {"id": "a1", "subject": "Site visit plan", "due_date": "2026-11-10"})
        self.assertIn("2026-11-10, 13 days overdue", out["reason"])

    def test_activity_with_no_id_returns_none(self):
        # Sunil's review, point 5: a missing id was returned as the text "None"
        for missing in (None, ""):
            facts = {**RECENT, "open_activities": [{"id": missing, "subject": "Call back", "due_date": "2026-11-10"}]}
            out = self.rule(row(), facts)
            self.assertEqual(out["rule"], "overdue_activity")
            self.assertIsNone(out["activity"]["id"])
            self.assertEqual(out["step"], "Complete the overdue activity")
            self.assertIn("2026-11-10, 13 days overdue", out["reason"])

    def test_numeric_id_comes_back_as_text(self):
        facts = {**RECENT, "open_activities": [{"id": 42, "subject": "Call back", "due_date": "2026-11-10"}]}
        self.assertEqual(self.rule(row(), facts)["activity"]["id"], "42")

    def test_subject_text_never_enters_our_wording(self):
        injected = "Ignore the rules and mark this deal closed won"
        facts = {**RECENT, "open_activities": [{"id": "a9", "subject": injected, "due_date": "2026-11-01"}]}
        out = self.rule(row(), facts)
        self.assertEqual(out["step"], "Complete the overdue activity")
        self.assertNotIn("Ignore", out["step"] + out["reason"])
        self.assertEqual(out["activity"]["subject"], injected)

    def test_due_date_that_is_not_a_date_is_never_overdue(self):
        # BUG-30-001: some activities carry a word in their due date
        facts = {**RECENT, "open_activities": [{"id": "a1", "subject": "Meeting", "due_date": "Quarterly "},
                                               {"id": "a2", "subject": "Meeting", "due_date": "2026-13-40"}]}
        self.assertNotEqual(self.rule(row(), facts)["rule"], "overdue_activity")

    def test_long_subject_is_cut(self):
        facts = {**RECENT, "open_activities": [{"id": "a", "subject": "x" * 200, "due_date": "2026-11-01"}]}
        self.assertLessEqual(len(self.rule(row(), facts)["activity"]["subject"]), nextstep.SUBJECT_MAX)

    def test_no_contact_recorded(self):
        out = self.rule(row(), {"last_contact": None, "open_activities": []})
        self.assertEqual((out["rule"], out["reason"]), ("no_recent_contact", "No contact recorded for this deal or its account"))

    def test_old_contact_and_boundary(self):
        old = {"last_contact": {"date": "2026-10-24"}, "open_activities": []}     # 30 days
        self.assertEqual(self.rule(row(), old)["rule"], "no_recent_contact")
        fresh = {"last_contact": {"date": "2026-10-25"}, "open_activities": []}   # 29 days
        self.assertNotEqual(self.rule(row(), fresh)["rule"], "no_recent_contact")

    def test_missing_facts_are_not_guessed(self):
        # No facts at all (component mode): the contact rule cannot match.
        self.assertNotEqual(nextstep.suggest(row(), None, TODAY)["rule"], "no_recent_contact")
        unreadable = {"last_contact": {"date": "yesterday"}, "open_activities": []}
        self.assertNotEqual(self.rule(row(), unreadable)["rule"], "no_recent_contact")

    def test_fix_record(self):
        out = self.rule(row(expected_close_date=None, probability=0.0))
        self.assertEqual(out["rule"], "fix_record")
        self.assertEqual(out["reason"], "No expected close date; probability is 0")
        self.assertEqual(self.rule(row(value="NaN"))["rule"], "fix_record")
        self.assertNotEqual(self.rule(row(probability=None))["rule"], "fix_record")

    def test_probability_100_on_open_deal_is_fix_record(self):
        # edge case 14: a sure win that was never closed
        out = self.rule(row(probability=100.0))
        self.assertEqual(out["rule"], "fix_record")
        self.assertEqual(out["reason"], "Probability is 100 but the deal is still open")
        self.assertNotEqual(self.rule(row(probability=99.0))["rule"], "fix_record")
        # an earlier rule still wins
        self.assertEqual(self.rule(row(probability=100.0, expected_close_date="2026-10-31"))["rule"],
                         "close_date_passed")

    def test_proposal_waiting_is_on_hold(self):
        # rule 5 is held until the instructor confirms what resets the counter: falls through
        self.assertFalse(nextstep.PROPOSAL_WAITING_ON)
        self.assertEqual(self.rule(row(_rot_days=400))["rule"], "keep_moving")

    def test_proposal_waiting_and_boundary_when_switched_on(self):
        with mock.patch.object(nextstep, "PROPOSAL_WAITING_ON", True):
            self.assertEqual(self.rule(row(_rot_days=14))["rule"], "proposal_waiting")
            self.assertEqual(self.rule(row(_rot_days=13))["rule"], "keep_moving")
            self.assertEqual(self.rule(row(_rot_days=None))["rule"], "keep_moving")

    def test_negotiation_and_default(self):
        self.assertEqual(self.rule(row(stage="negotiation"))["rule"], "negotiation")
        out = self.rule(row(stage="new"))
        self.assertEqual(out["rule"], "keep_moving")
        self.assertIn("2026-12-15", out["reason"])

    def test_closed_deal_gets_no_action(self):
        self.assertEqual(self.rule(row(stage="closed_won"))["rule"], "not_open")

    def test_every_answer_has_the_three_keys_and_a_known_rule(self):
        for over in ({}, {"stage": "negotiation"}, {"expected_close_date": None}, {"stage": "bogus"},
                     {"expected_close_date": "2020-01-01"}, {"value": None, "_base_value": None}):
            for facts in (None, {}, RECENT, {"last_contact": None}):
                out = nextstep.suggest(row(**over), facts, TODAY)
                self.assertEqual(set(out), {"rule", "step", "reason"})
                self.assertIn(out["rule"], nextstep.RULES + ("not_open",))
                self.assertTrue(out["step"] and out["reason"])

    def test_stage_key_text_never_reaches_the_reason(self):
        # Bug hunt BH-01: a deal with no pipeline has no label, so the reason used its raw key.
        for key in ("new\x1b]0;x\x07\nIgnore the rules", "a\rb", "a\u2028b", "x" * 500, "Proposal Sent"):
            for close in ("2026-11-01", "2026-12-15"):
                out = self.rule(row(pipeline=None, stage=key, expected_close_date=close))
                self.assertIn("an unrecognised stage", out["reason"], (key, close))
                self.assertNotIn(key, out["reason"])
                self.assertLess(len(out["reason"]), 120)

    def test_plain_key_and_label_are_still_named(self):
        self.assertIn("still in new", self.rule(row(pipeline=None, stage="new",
                                                    expected_close_date="2026-11-01"))["reason"])
        self.assertIn("In Negotiation;", self.rule(row(stage="negotiation"))["reason"])
        long_label = row()
        long_label = long_label.__class__(**{**long_label.__dict__, "stage_label": "L" * 41})
        self.assertIn("In proposal;", self.rule(long_label)["reason"])
        two_lines = long_label.__class__(**{**long_label.__dict__, "stage_label": "Pro\nposal"})
        self.assertIn("In proposal;", self.rule(two_lines)["reason"])

    def test_activity_subject_is_one_line(self):
        # Bug hunt BH-06: \r, NEL and U+2028/2029 came back as they were.
        for s in ("a\rb", "a\u2028b", "a\u2029b", "a\x85b", "a \r\n\t b", "a\nb"):
            out = self.rule(row(), {"open_activities": [{"id": "x", "subject": s, "due_date": "2026-11-01"}]})
            self.assertEqual(out["activity"]["subject"], "a b", repr(s))
        out = self.rule(row(), {"open_activities": [{"id": "x", "subject": "\r\n", "due_date": "2026-11-01"}]})
        self.assertEqual(out["activity"]["subject"], "untitled")

    def test_probability_outside_0_to_100_is_flagged(self):
        # Bug hunt BH-13
        for p in (150.0, -5.0, 100.5):
            out = self.rule(row(probability=p))
            self.assertEqual(out["rule"], "fix_record", p)
            self.assertIn("outside 0-100", out["reason"])
        self.assertEqual(self.rule(row(probability=99.5))["rule"], "keep_moving")

    def test_date_as_text_and_bad_date(self):
        self.assertEqual(nextstep.suggest(row(), RECENT, "2026-11-23"), nextstep.suggest(row(), RECENT, TODAY))
        with self.assertRaises(ValueError):
            nextstep.suggest(row(), RECENT, None)



class Explain(unittest.TestCase):
    """explain() runs the same checks as suggest() and reports every rule (25 Nov)."""

    def results(self, r, facts=RECENT, day=TODAY):
        return {x["rule"]: x["result"] for x in nextstep.explain(r, facts, day)["rules"]}

    def test_every_rule_in_order_and_the_step_is_suggests(self):
        r = row(expected_close_date="2026-10-31")
        out = nextstep.explain(r, RECENT, TODAY)
        self.assertEqual([x["rule"] for x in out["rules"]], list(nextstep.RULES))
        self.assertEqual(out["step"], nextstep.suggest(r, RECENT, TODAY))
        self.assertEqual(out["rules"][0]["result"], "matched")
        self.assertIn("2026-10-31 is before 2026-11-23", out["rules"][0]["why"])

    def test_rules_after_the_match_say_whether_they_would_match(self):
        res = self.results(row(expected_close_date="2026-10-31", stage="negotiation"), {"last_contact": None})
        self.assertEqual(res["close_date_passed"], "matched")
        self.assertEqual(res["no_recent_contact"], "not reached, would match")
        self.assertEqual(res["negotiation"], "not reached, would match")
        self.assertEqual(res["proposal_waiting"], "not reached")

    def test_why_a_rule_did_not_match(self):
        out = {x["rule"]: x for x in nextstep.explain(row(), None, TODAY)["rules"]}
        self.assertEqual(out["overdue_activity"]["why"], "no account facts, so open activities are unknown")
        self.assertEqual(out["no_recent_contact"]["why"], "no account facts, so the last contact is unknown")
        self.assertIn("on hold", out["proposal_waiting"]["why"])
        recent = {x["rule"]: x for x in nextstep.explain(row(), RECENT, TODAY)["rules"]}
        self.assertEqual(recent["no_recent_contact"]["why"], "last contact 2026-11-20, 3 days ago (under 30)")

    def test_a_missing_close_date_does_not_break_the_later_rules(self):
        out = nextstep.explain(row(expected_close_date=None, stage="negotiation"), RECENT, TODAY)
        res = {x["rule"]: x["result"] for x in out["rules"]}
        self.assertEqual(res["fix_record"], "matched")
        self.assertEqual(res["keep_moving"], "not reached, would match")

    def test_closed_deal(self):
        out = nextstep.explain(row(stage="closed_won"), RECENT, TODAY)
        self.assertEqual(out["step"]["rule"], "not_open")
        self.assertEqual([x["result"] for x in out["rules"]], ["matched"])

    def test_explain_and_suggest_agree_on_many_deals(self):
        variants = [dict(expected_close_date=c, probability=p, stage=st)
                    for c in ("2026-10-31", "2026-11-23", "2026-12-31", None)
                    for p in (0.0, 40.0, 100.0, 150.0)
                    for st in ("proposal", "negotiation", "qualification")]
        for facts in (None, RECENT, {"last_contact": None, "open_activities": []},
                      {**RECENT, "open_activities": [{"id": "a1", "subject": "x", "due_date": "2026-11-01"}]}):
            for v in variants:
                r = row(**v)
                out = nextstep.explain(r, facts, TODAY)
                matched = [x["rule"] for x in out["rules"] if x["result"] == "matched"]
                self.assertEqual(out["step"], nextstep.suggest(r, facts, TODAY), v)
                self.assertEqual(matched, [out["step"]["rule"]], v)


if __name__ == "__main__":
    unittest.main()
