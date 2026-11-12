"""Pagination protocol and complete-report regression tests, no platform/network."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from o2c.client import Client, McpError
from o2c import __main__ as cli


def rows(*ids):
    return [{"id": value} for value in ids]


class Pagination(unittest.TestCase):
    def client(self):
        return Client(base_url="https://synthetic.invalid", token="synthetic")

    def test_missing_total_never_returns_a_partial_result(self):
        c = self.client()
        for page in ({"data": rows("a")}, {"data": []}):
            with self.subTest(page=page), patch.object(c, "call", return_value=page), self.assertRaises(McpError):
                c.list_all("Invoice")

    def test_missing_total_on_later_page_is_not_success(self):
        pages = [{"data": rows("a"), "total": 2}, {"data": rows("b")}]
        with patch.object(Client, "call", side_effect=pages), self.assertRaises(McpError):
            self.client().list_all("Invoice")

    def test_empty_checked_list(self):
        c = self.client()
        with patch.object(c, "call", return_value={"data": [], "total": 0}) as call:
            self.assertEqual(c.list_all("Invoice"), [])
        self.assertEqual(call.call_count, 1)

    def test_full_and_final_pages_preserve_filters_and_offsets(self):
        c = self.client()
        pages = [{"data": rows("a", "b"), "total": 3}, {"data": rows("c"), "total": 3}]
        with patch("o2c.client.PAGE_SIZE", 2), patch.object(c, "call", side_effect=pages) as call:
            self.assertEqual(c.list_all("Invoice", {"docstatus": 1, "offset": 99, "limit": 99}), rows("a", "b", "c"))
        self.assertEqual(call.call_args_list[0].args, ("Invoice.list", {"docstatus": 1, "offset": 0, "limit": 2}))
        self.assertEqual(call.call_args_list[1].args, ("Invoice.list", {"docstatus": 1, "offset": 2, "limit": 2}))

    def test_default_page_size_does_not_drop_record_501(self):
        c = self.client()
        pages = [{"data": rows(*range(500)), "total": 501}, {"data": rows(500), "total": 501}]
        with patch.object(c, "call", side_effect=pages):
            result = c.list_all("SalesOrder")
        self.assertEqual(len(result), 501)
        self.assertEqual(result[-1], {"id": 500})

    def test_invalid_totals_rejected(self):
        c = self.client()
        for total in (None, "2", -1, True, False, 2.0, 1.5, [], {}):
            with self.subTest(total=total), patch.object(c, "call", return_value={"data": rows("a"), "total": total}), self.assertRaises(McpError):
                c.list_all("Payment")

    def test_zero_total_with_data_rejected(self):
        with patch.object(Client, "call", return_value={"data": rows("a"), "total": 0}), self.assertRaises(McpError):
            self.client().list_all("Payment")

    def test_early_empty_page_is_not_success(self):
        pages = [{"data": rows("a"), "total": 2}, {"data": [], "total": 2}]
        with patch.object(Client, "call", side_effect=pages), self.assertRaises(McpError):
            self.client().list_all("Invoice")

    def test_short_page_does_not_override_an_advertised_total(self):
        pages = [{"data": rows("a"), "total": 3}, {"data": rows("b", "c"), "total": 3}]
        with patch.object(Client, "call", side_effect=pages):
            self.assertEqual(self.client().list_all("Invoice"), rows("a", "b", "c"))

    def test_changing_total_rejected(self):
        for next_total in (1, 3):
            pages = [{"data": rows("a"), "total": 2}, {"data": rows("b"), "total": next_total}]
            with self.subTest(total=next_total), patch.object(Client, "call", side_effect=pages), self.assertRaises(McpError):
                self.client().list_all("Invoice")

    def test_more_rows_than_total_rejected(self):
        with patch.object(Client, "call", return_value={"data": rows("a", "b"), "total": 1}), self.assertRaises(McpError):
            self.client().list_all("Invoice")

    def test_duplicate_ids_within_or_across_pages_rejected(self):
        for pages in ([{"data": rows("a", "a"), "total": 2}],
                      [{"data": rows("a"), "total": 2}, {"data": rows("a"), "total": 2}]):
            with self.subTest(pages=pages), patch.object(Client, "call", side_effect=pages), self.assertRaises(McpError):
                self.client().list_all("Invoice")

    def test_repeated_page_without_ids_rejected(self):
        page = {"data": [{"name": "synthetic"}], "total": 2}
        with patch.object(Client, "call", side_effect=[page, page]), self.assertRaises(McpError):
            self.client().list_all("Invoice")

    def test_bad_page_and_record_shapes_rejected(self):
        for page in (None, [], {"total": 0}, {"total": 1, "data": None},
                     {"total": 1, "data": {}}, {"total": 1, "data": ["not a record"]}):
            with self.subTest(page=page), patch.object(Client, "call", return_value=page), self.assertRaises(McpError):
                self.client().list_all("Invoice")

    def test_page_budget_is_bounded(self):
        pages = [{"data": rows("a"), "total": 3}, {"data": rows("b"), "total": 3}]
        with patch.object(Client, "call", side_effect=pages) as call, self.assertRaises(McpError):
            self.client().list_all("Invoice", max_pages=2)
        self.assertEqual(call.call_count, 2)

    def test_bad_page_budget_rejected_before_read(self):
        for maximum in (0, -1, True, "2"):
            with self.subTest(maximum=maximum), patch.object(Client, "call") as call, self.assertRaises(ValueError):
                self.client().list_all("Invoice", max_pages=maximum)
            call.assert_not_called()

    def test_transport_failure_after_first_page_propagates(self):
        with patch.object(Client, "call", side_effect=[{"data": rows("a"), "total": 2}, McpError("synthetic failure")]), self.assertRaisesRegex(McpError, "synthetic failure"):
            self.client().list_all("Invoice")


class ReportCompleteness(unittest.TestCase):
    def run_cli(self, pages, target):
        client = Client(base_url="https://synthetic.invalid", token="synthetic")
        argv = ["o2c", "--date", "2026-11-15", "--instance", "synthetic", "--out", str(target)]
        with patch.object(client, "tool_names", return_value={"SalesOrder.list", "Payment.list"}), \
                patch.object(client, "call", side_effect=pages), patch.object(cli, "Client", return_value=client), \
                patch("sys.argv", argv), contextlib.redirect_stdout(io.StringIO()) as output:
            cli.main()
        return output.getvalue()

    @staticmethod
    def order(number):
        return {"id": number, "number": number, "date": "2026-11-01", "docstatus": 1,
                "invoiced_status": "unbilled", "grand_total": 100, "currency_code": "INR"}

    def test_incomplete_source_publishes_no_report_pair(self):
        for pages in ([{"data": [self.order("SO-1")]}],
                      [{"data": [self.order("SO-1")], "total": 2}, {"data": [], "total": 2}]):
            with self.subTest(pages=pages), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "reports"
                with self.assertRaises(McpError):
                    self.run_cli(pages, target)
                self.assertFalse(target.exists())

    def test_complete_pages_publish_matching_json_and_markdown(self):
        pages = [{"data": [self.order("SO-1")], "total": 2},
                 {"data": [self.order("SO-2")], "total": 2}, {"data": [], "total": 0}]
        with tempfile.TemporaryDirectory() as tmp:
            output = self.run_cli(pages, Path(tmp))
            result = json.loads((Path(tmp) / "synthetic-2026-11-15.json").read_text(encoding="utf-8"))
            md = (Path(tmp) / "synthetic-2026-11-15.md").read_text(encoding="utf-8")
        self.assertEqual([r["id"] for r in result["sections"]["orders_not_invoiced"]["rows"]], ["SO-1", "SO-2"])
        self.assertIn("## Orders not invoiced (2)", md)
        self.assertIn("| SO-1 |", md)
        self.assertIn("| SO-2 |", md)
        self.assertIn("orders_not_invoiced=2", output)

    def test_payment_pagination_failure_preserves_existing_reports(self):
        # SalesOrder succeeds, then Payment's advertised second row is absent.
        pages = [{"data": [self.order("SO-1")], "total": 1},
                 {"data": [{"id": "PAY-1"}], "total": 2}, {"data": [], "total": 2}]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            paths = [target / "synthetic-2026-11-15.json", target / "synthetic-2026-11-15.md"]
            for p in paths:
                p.write_text("previous complete output", encoding="utf-8")
            with self.assertRaises(McpError):
                self.run_cli(pages, target)
            self.assertEqual([p.read_text(encoding="utf-8") for p in paths], ["previous complete output"] * 2)


if __name__ == "__main__":
    unittest.main()
