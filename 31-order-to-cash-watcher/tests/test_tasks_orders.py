"""Suite tasks 1-4 against a fake platform (no network, made-up rows).

Each verifier is shown passing on a correct report and failing on a report with a planted
fault, so a verifier that always passes cannot hide here.
"""
import unittest
from datetime import date
from unittest import mock

from o2c import report
from o2c import tasks_orders as T
from tests.test_tasks_payments import FakePlatform

D = date(2026, 11, 15)


def order(id_, day, status="unbilled", docstatus=1, total=100.0):
    return {"id": id_, "number": id_, "date": day, "invoiced_status": status, "docstatus": docstatus,
            "grand_total": total, "currency_code": None, "party_id": "P1", "company_id": "C1",
            "_party_id_display": "Acme", "delivered_status": "not_delivered"}


ORDERS = [
    order("A", "2026-11-01"),                                  # age 14: listed
    order("B", "2026-11-08"),                                  # age 7: on the N = 7 boundary, not listed
    order("C", "2026-11-07", status="partially_invoiced"),     # age 8: listed, partially invoiced
    order("D", "2026-10-01", docstatus=0),                     # draft
    order("E", "2026-10-01", docstatus=2),                     # cancelled
    order("F", "2026-10-01", status="invoiced"),               # fully invoiced
    order("G", None),                                          # missing date: a date issue, not a row
    order("H", "2026-11-20"),                                  # future date: a date issue, not a row
]


def ctx(orders=ORDERS, days=7, tools=None, platform=FakePlatform):
    fake = platform({"SalesOrder": orders, "Payment": [], "EmailMessage": []})
    if tools is not None:
        fake.tools = set(tools)
    return T.Ctx(fake, "t", D, days)


def wrong_rule(op):
    """report.orders_not_invoiced with a planted fault: `op` decides which open orders are listed."""
    real = report.orders_not_invoiced

    def fake(orders, report_date, days, issues=None):
        rows = real(orders, report_date, 0, issues)   # every usable open order of age >= 1
        return [r for r in rows if op(r["age_days"], days)]
    return mock.patch.object(report, "orders_not_invoiced", fake)


class OrderTasksPass(unittest.TestCase):
    def test_all_pass_on_a_correct_report(self):
        results = {r["id"]: r for r in T.run(ctx(), T.TASKS)}
        self.assertEqual(set(results), {f"t:{t[0]}" for t in T.TASKS})
        for rid, r in results.items():
            self.assertTrue(r["passed"], f"{rid}: {r['evidence']}")
            self.assertEqual(r["score"], 1.0)

    def test_count_evidence_names_the_recount(self):
        r = T.uninvoiced_count(ctx())
        self.assertIn("expected=2 got=2", r["evidence"])
        self.assertIn("date_issues expected=2 got=2", r["evidence"])

    def test_threshold_uses_the_order_closest_to_n(self):
        r = T.uninvoiced_threshold(ctx())
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("boundary_age=7", r["evidence"])
        self.assertIn("N=6: expected=3 got=3", r["evidence"])
        self.assertIn("N=7: expected=2 got=2", r["evidence"])

    def test_excludes_counts_each_kind(self):
        r = T.uninvoiced_excludes_drafts(ctx())
        self.assertIn("draft=1 cancelled=1 invoiced=1", r["evidence"])



class OrderTasksCatchFaults(unittest.TestCase):
    def test_count_fails_when_the_boundary_order_is_included(self):
        with wrong_rule(lambda age, n: age >= n):
            r = T.uninvoiced_count(ctx())
        self.assertFalse(r["passed"])
        self.assertIn("extra=['B']", r["evidence"])

    def test_count_fails_when_a_server_filter_drops_a_row(self):
        class DroppingFilter(FakePlatform):
            def list_all(self, entity, filters=None):
                rows = super().list_all(entity, filters)
                return [r for r in rows if r["id"] != "A"] if filters else rows   # only the report's filtered read
        r = T.uninvoiced_count(ctx(platform=DroppingFilter))
        self.assertFalse(r["passed"])
        self.assertIn("missing=['A']", r["evidence"])

    def test_count_fails_on_a_wrong_age(self):
        c = ctx()
        rep = c.report()
        rep["sections"]["orders_not_invoiced"]["rows"][0]["age_days"] += 1
        r = T.uninvoiced_count(c)
        self.assertFalse(r["passed"])
        self.assertIn("wrong_age=['A']", r["evidence"])

    def test_count_fails_on_a_duplicated_row(self):
        c = ctx()
        rows = c.report()["sections"]["orders_not_invoiced"]["rows"]
        rows.append(dict(rows[0]))
        self.assertFalse(T.uninvoiced_count(c)["passed"])

    def test_count_fails_when_an_unusable_date_is_not_reported(self):
        c = ctx()
        c.report()["sections"]["orders_not_invoiced"]["date_issues"] = []
        r = T.uninvoiced_count(c)
        self.assertFalse(r["passed"])
        self.assertIn("date_issues expected=2 got=0", r["evidence"])

    def test_threshold_fails_when_the_boundary_is_inclusive(self):
        with wrong_rule(lambda age, n: age >= n):
            r = T.uninvoiced_threshold(ctx())
        self.assertFalse(r["passed"])
        self.assertIn("boundary_ok=False", r["evidence"])

    def test_threshold_fails_when_n_is_ignored(self):
        with wrong_rule(lambda age, n: age > 7):
            r = T.uninvoiced_threshold(ctx(days=10))   # boundary moves to age 8 (C); N=7 is hard-coded
        self.assertFalse(r["passed"], r["evidence"])

    def test_excludes_fails_when_a_draft_leaks(self):
        c = ctx()
        c.report()["sections"]["orders_not_invoiced"]["rows"].append(dict(order("D", "2026-10-01", docstatus=0)))
        r = T.uninvoiced_excludes_drafts(c)
        self.assertFalse(r["passed"])
        self.assertIn("leaked=['D']", r["evidence"])

    def test_excludes_fails_when_a_fully_invoiced_order_is_a_date_issue(self):
        c = ctx()
        c.report()["sections"]["orders_not_invoiced"]["date_issues"].append({"id": "F", "number": "F", "reason": "x"})
        self.assertFalse(T.uninvoiced_excludes_drafts(c)["passed"])

    def test_partial_fails_when_the_partial_order_is_missing(self):
        c = ctx()
        sec = c.report()["sections"]["orders_not_invoiced"]
        sec["rows"] = [r for r in sec["rows"] if r["id"] != "C"]
        r = T.uninvoiced_partial(c)
        self.assertFalse(r["passed"])
        self.assertIn("missing=['C']", r["evidence"])

    def test_partial_fails_when_the_status_is_relabelled(self):
        c = ctx()
        for r in c.report()["sections"]["orders_not_invoiced"]["rows"]:
            r["invoiced_status"] = "unbilled"
        r = T.uninvoiced_partial(c)
        self.assertFalse(r["passed"])
        self.assertIn("mislabelled=['C']", r["evidence"])



class OrderTasksEdges(unittest.TestCase):
    def test_blocked_without_sales_order_read(self):
        c = ctx(tools={"Payment.list", "EmailMessage.list"})
        for fn in (T.uninvoiced_count, T.uninvoiced_threshold, T.uninvoiced_excludes_drafts, T.uninvoiced_partial):
            r = fn(c)
            self.assertFalse(r["passed"])
            self.assertTrue(r["evidence"].startswith("outcome=blocked"), r["evidence"])

    def test_vacuous_passes_are_labelled(self):
        only_open = [order("A", "2026-11-01")]
        for fn in (T.uninvoiced_excludes_drafts, T.uninvoiced_partial):
            r = fn(ctx(orders=only_open))
            self.assertTrue(r["passed"], r["evidence"])
            self.assertIn("vacuous", r["evidence"])
        r = T.uninvoiced_threshold(ctx(orders=[order("Z", "2026-11-15")]))   # age 0 only
        self.assertTrue(r["passed"])
        self.assertIn("vacuous", r["evidence"])

    def test_threshold_at_zero_days(self):
        r = T.uninvoiced_threshold(ctx(orders=[order("Y", "2026-11-14")], days=0))   # age 1, boundary N=0..1
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("boundary_age=1", r["evidence"])

    def test_unreadable_date_is_a_date_issue_not_a_row(self):
        r = T.uninvoiced_count(ctx(orders=[order("A", "2026-11-01"), order("X", "not-a-date")]))
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("expected=1 got=1", r["evidence"])
        self.assertIn("date_issues expected=1 got=1", r["evidence"])

    def test_timestamp_dates_keep_their_calendar_day(self):
        r = T.uninvoiced_count(ctx(orders=[order("T", "2026-11-07T23:30:00+05:30")]))
        self.assertTrue(r["passed"], r["evidence"])
        self.assertIn("expected=1 got=1", r["evidence"])

    def test_crash_becomes_infrastructure_failure(self):
        c = ctx()
        c.client.list_all = mock.Mock(side_effect=RuntimeError("boom"))
        r = T.run(c, T.TASKS[:1])[0]
        self.assertFalse(r["passed"])
        self.assertIn("outcome=infrastructure; RuntimeError: boom", r["evidence"])

    def test_tasks_only_read(self):
        c = ctx()
        T.run(c, T.TASKS)
        self.assertNotIn("EmailMessage.create", c.client.requests)


class OrderTasksCli(unittest.TestCase):
    def test_invalid_days_rejected_before_a_client_is_made(self):
        with mock.patch("sys.argv", ["o2c.tasks_orders", "--instance", "t", "--date", "2026-11-15", "--days", "-1"]), \
             mock.patch.object(T, "Client") as client, mock.patch("sys.stderr"):
            with self.assertRaises(SystemExit):
                T.main()
        client.assert_not_called()

    def test_main_prints_and_writes_results(self):
        import json
        import os
        import tempfile
        fake = FakePlatform({"SalesOrder": ORDERS, "Payment": [], "EmailMessage": []})
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "r.json")
            with mock.patch("sys.argv", ["o2c.tasks_orders", "--instance", "t", "--date", "2026-11-15", "--out", out]), \
                 mock.patch.object(T, "Client", return_value=fake), mock.patch("builtins.print") as printed:
                T.main()
            with open(out, encoding="utf-8") as f:
                saved = json.load(f)
        self.assertEqual(saved["summary"], "t: 4/4 tasks passed")
        printed.assert_any_call("t: 4/4 tasks passed")


if __name__ == "__main__":
    unittest.main()
