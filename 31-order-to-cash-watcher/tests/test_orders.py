"""Offline tests for section A using synthetic orders (no platform data, no network)."""
import unittest
from datetime import date

from otc_watcher.orders import unbilled_orders
from otc_watcher.report import render

TODAY = date(2026, 11, 6)


def order(i, d, **kw):
    base = dict(id=f"id{i}", number=f"SO-{i}", date=d, docstatus=1, invoiced_status="unbilled",
                status="confirmed", delivered_status="undelivered", grand_total=100, currency_code="INR",
                _party_id_display="Acme")
    return {**base, **kw}


class UnbilledOrders(unittest.TestCase):
    def test_older_than_n_days_is_reported_and_sorted_oldest_first(self):
        rows = unbilled_orders([order(1, "2026-11-01"), order(2, "2026-10-01"), order(3, "2026-10-20")], TODAY, 7)
        self.assertEqual([r["number"] for r in rows], ["SO-2", "SO-3"])
        self.assertEqual(rows[0]["age_days"], 36)

    def test_exactly_n_days_old_is_not_yet_an_exception(self):
        self.assertEqual(unbilled_orders([order(1, "2026-10-30")], TODAY, 7), [])
        self.assertEqual(len(unbilled_orders([order(1, "2026-10-29")], TODAY, 7)), 1)

    def test_invoiced_draft_cancelled_and_undated_orders_are_ignored(self):
        orders = [order(1, "2026-09-01", invoiced_status="invoiced"), order(2, "2026-09-01", docstatus=0),
                  order(3, "2026-09-01", docstatus=2), order(4, "2026-09-01", status="draft"),
                  order(5, None), order(6, "2026-09-01", status="cancelled")]
        self.assertEqual(unbilled_orders(orders, TODAY, 7), [])

    def test_delivered_but_unbilled_is_reported(self):
        rows = unbilled_orders([order(1, "2026-09-01", status="delivered", delivered_status="delivered")], TODAY, 7)
        self.assertEqual(len(rows), 1)

    def test_timestamp_dates_are_accepted(self):
        self.assertEqual(len(unbilled_orders([order(1, "2026-09-01T10:00:00Z")], TODAY, 7)), 1)

    def test_empty_result_renders_cleanly(self):
        self.assertIn("_None._", render("keystone", TODAY, 7, []))

    def test_render_lists_rows(self):
        rows = unbilled_orders([order(1, "2026-09-01")], TODAY, 7)
        text = render("suryodaya", TODAY, 7, rows)
        self.assertIn("SO-1", text)
        self.assertIn("(1)", text)


if __name__ == "__main__":
    unittest.main()
