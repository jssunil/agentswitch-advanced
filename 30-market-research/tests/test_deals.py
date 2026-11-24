import unittest
from decimal import Decimal

from brief import deals, nextstep
from brief.rows import from_record, stage_labels
from tests.fakes import PIPELINES, deal


def rows(*records):
    labels = stage_labels(PIPELINES)
    return [from_record(r, labels) for r in records]


def issues_for(issues, deal_id):
    return [i["issue"] for i in issues if i["deal_id"] == deal_id]


class OpenDealsTest(unittest.TestCase):
    def test_ranks_by_base_value_largest_first(self):
        ranked, _ = deals.open_deals(rows(deal(1), deal(3), deal(2)))
        self.assertEqual([r.id for r in ranked], ["d03", "d02", "d01"])

    def test_ranks_on_base_value_not_own_value(self):
        ranked, _ = deals.open_deals(rows(deal(1, value=999999, currency="USD"), deal(2)))
        self.assertEqual([r.id for r in ranked], ["d02", "d01"])

    def test_closed_deals_are_left_out_without_issues(self):
        ranked, issues = deals.open_deals(rows(deal(1), deal(2, stage="closed_won"), deal(3, stage="closed_lost")))
        self.assertEqual([r.id for r in ranked], ["d01"])
        self.assertEqual(issues, [])

    def test_unknown_stage_is_an_issue_never_ranked(self):
        ranked, issues = deals.open_deals(rows(deal(1), deal(9, stage="nope")))
        self.assertEqual([r.id for r in ranked], ["d01"])
        self.assertIn(deals.LEFT_OUT_STAGE, issues_for(issues, "d09"))
        self.assertIn("stage: not a stage of the deal's pipeline", issues_for(issues, "d09"))

    def test_missing_pipeline_is_open_and_ranked_with_its_issue(self):
        ranked, issues = deals.open_deals(rows(deal(1, pipeline=None), deal(2, pipeline="default")))
        self.assertEqual([r.id for r in ranked], ["d02", "d01"])
        self.assertTrue(issues_for(issues, "d01") and issues_for(issues, "d02"))
        self.assertFalse(set(deals.LEFT_OUT) & {i["issue"] for i in issues})

    def test_closed_stage_with_no_pipeline_is_closed(self):
        ranked, issues = deals.open_deals(rows(deal(1, pipeline=None, stage="closed_won")))
        self.assertEqual((ranked, issues), ([], []))

    def test_value_zero_or_less_is_judged_on_value(self):
        ranked, issues = deals.open_deals(rows(deal(1), deal(2, value=0, _base_value=50000),
                                               deal(3, value=-5, _base_value=50000)))
        self.assertEqual([r.id for r in ranked], ["d01"])
        for i in ("d02", "d03"):
            self.assertEqual(issues_for(issues, i), [deals.LEFT_OUT_VALUE_NOT_POSITIVE])

    def test_unreadable_value(self):
        ranked, issues = deals.open_deals(rows(deal(1, value="abc"), deal(2, value=None)))
        self.assertEqual(ranked, [])
        self.assertIn(deals.LEFT_OUT_VALUE_UNREADABLE, issues_for(issues, "d01"))
        self.assertIn("value: not readable", issues_for(issues, "d01"))
        self.assertEqual(issues_for(issues, "d02"), [deals.LEFT_OUT_VALUE_UNREADABLE])

    def test_positive_value_without_usable_base_is_a_currency_issue(self):
        ranked, issues = deals.open_deals(rows(
            deal(1, _base_value=None), deal(2, currency="ZZZ", _base_value=0), deal(3, currency=None),
            deal(4, _base_currency=None), deal(5, currency="RUPEES")))
        self.assertEqual(ranked, [])
        for i in ("d01", "d02", "d03", "d04", "d05"):
            self.assertIn(deals.LEFT_OUT_CURRENCY, issues_for(issues, i))

    def test_one_reason_per_deal(self):
        # zero value and an unknown currency: counted once, under value (the first rule that applies)
        _, issues = deals.open_deals(rows(deal(1, value=0, currency="ZZZ", _base_value=0)))
        self.assertEqual([i for i in issues_for(issues, "d01") if i in deals.LEFT_OUT],
                         [deals.LEFT_OUT_VALUE_NOT_POSITIVE])

    def test_tie_break_by_id_as_text(self):
        ranked, _ = deals.open_deals(rows(deal(1, id="D-9", _base_value=500), deal(2, id="D-10", _base_value=500),
                                          deal(3, id="D-1", _base_value=400)))
        self.assertEqual([r.id for r in ranked], ["D-10", "D-9", "D-1"])

    def test_order_does_not_depend_on_input_order(self):
        records = [deal(i, _base_value=1000 * (i % 3)) for i in range(1, 9)]
        a, _ = deals.open_deals(rows(*records))
        b, _ = deals.open_deals(rows(*reversed(records)))
        self.assertEqual([r.id for r in a], [r.id for r in b])

    def test_closed_set_matches_nextstep(self):
        self.assertEqual(set(deals.CLOSED), set(nextstep.CLOSED))


class GroupsTest(unittest.TestCase):
    def test_one_list_per_company_and_base_currency(self):
        records = [deal(i, _base_value=100 * i) for i in range(1, 4)] + \
                  [deal(i, company_id="c2", _base_currency="USD", currency="USD", pipeline=None, _base_value=10 * i)
                   for i in range(4, 7)]
        ranked, _ = deals.open_deals(rows(*records))
        self.assertEqual([r.id for r in deals.top(ranked, 2)], ["d03", "d02", "d06", "d05"])
        self.assertEqual([(c, cur, len(g)) for c, cur, g in deals.groups(ranked)],
                         [("c1", "INR", 3), ("c2", "USD", 3)])

    def test_fewer_than_n(self):
        ranked, _ = deals.open_deals(rows(deal(1), deal(2)))
        self.assertEqual(len(deals.top(ranked, 10)), 2)

    def test_top_needs_positive_n(self):
        with self.assertRaises(ValueError):
            deals.top([], 0)

    def test_tied_beyond_cut_off(self):
        records = [deal(i, _base_value=500 if i >= 3 else 100 * i) for i in range(1, 7)]
        ranked, _ = deals.open_deals(rows(*records))   # four deals at 500
        self.assertEqual(deals.tied_beyond(ranked, 2), {("c1", "INR"): 2})
        self.assertEqual(deals.tied_beyond(ranked, 4), {})
        self.assertEqual(deals.tied_beyond(ranked, 10), {})


class FixtureTest(unittest.TestCase):
    def test_rules(self):
        flagged = {
            "[team99-test X-01] D1": "test marker",
            "T9-probe-deal-1": "probe prefix",
            "t12-probe": "probe prefix",
            "Acme Ltd — 12345678": "generated suffix",
            "Acme Ltd — QA team — 1a2b3c4d": "generated suffix",
            "Acme - deadbeef": "generated suffix",
            "Deal from 00000000-1111-2222-3333-444444444444": "generated title",
        }
        for title, rule in flagged.items():
            self.assertEqual(deals.fixture_reason(title), rule, title)
        for title in ("Acme — enclosure second source", "Acme — heat sink, phase 2",
                      "Contest bracket", "Latest order", "Testing rig", "Despatch, week 36",
                      "Acme — 1a2b3c4", "Deal from Acme", "", None):
            self.assertIsNone(deals.fixture_reason(title), title)

    def test_fixtures_are_ranked_and_flagged(self):
        ranked, _ = deals.open_deals(rows(deal(1), deal(2, title="T6-probe")))
        self.assertEqual([(r.id, r.fixture_flag) for r in ranked], [("d02", True), ("d01", False)])


if __name__ == "__main__":
    unittest.main()
