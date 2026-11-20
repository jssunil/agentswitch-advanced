"""Phase 2 item 1: the CLI prints the order record ids, one per line, for seat 34's 30 Nov recount."""
import contextlib
from datetime import date, timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from o2c import __main__ as cli
from o2c import output

D = date(2026, 11, 15)


def order(oid, age=8, **extra):
    row = {"id": oid, "number": f"SO-{oid}", "date": (D - timedelta(days=age)).isoformat(), "docstatus": 1,
           "invoiced_status": "unbilled", "grand_total": 100, "currency_code": "INR", "_party_id_display": "Customer"}
    row.update(extra)
    return row


class Orders:
    """A read-only client with SalesOrder rows only."""

    def __init__(self, orders):
        self.orders = orders

    def tool_names(self):
        return {"SalesOrder.list"}

    def list_all(self, entity, filters=None):
        return list(self.orders) if entity == "SalesOrder" else []


def split_result(stdout):
    """(stdout without the AGENT_RESULT line, the parsed result). Checks the contract on every run:
    exactly one AGENT_RESULT line, it is the last line, and its answer is the id list printed above."""
    lines = stdout.splitlines()
    results = [i for i, line in enumerate(lines) if line.startswith("AGENT_RESULT ")]
    assert len(results) == 1 and results[0] == len(lines) - 1, f"expected one AGENT_RESULT line, last: {lines[-3:]}"
    result = json.loads(lines[-1][len("AGENT_RESULT "):])
    body = lines[:-1]
    ids = [line for line in body if not line.startswith("#")] if body and not body[0].startswith("#") else None
    if ids is not None:   # the id list (not --markdown): the answer repeats it exactly
        assert result["answer"]["orders_not_invoiced"] == ids, (result, ids)
    return "".join(line + "\n" for line in body), result


def run(orders, *extra):
    """(exit code, stdout, stderr, report JSON or None) of one CLI run on synthetic orders."""
    with tempfile.TemporaryDirectory() as tmp:
        argv = ["o2c", "--date", D.isoformat(), "--days", "7", "--instance", "synthetic", *extra]
        argv = [a.replace("{tmp}", tmp) for a in argv]
        with patch.object(cli, "Client", return_value=Orders(orders)), patch("sys.argv", argv), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            try:
                cli.main()
                code = 0
            except SystemExit as e:
                code = e.code
        path = Path(tmp) / f"synthetic-{D.isoformat()}.json"
        rep = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    stdout, run.result = split_result(out.getvalue())
    return code, stdout, err.getvalue(), rep


class OrderIdList(unittest.TestCase):
    def test_stdout_is_only_the_ids_oldest_first(self):
        code, out, err, _ = run([order("a", age=10), order("b", age=30), order("c", age=3), order("d", age=8)])
        self.assertEqual(code, 0)
        self.assertEqual(out, "b\na\nd\n")        # c is too recent (3 <= 7 days)
        self.assertIn("3 orders not invoiced after 7 days on 2026-11-15", err)
        self.assertNotIn("#", out)                 # no Markdown on stdout

    def test_ids_match_the_report_json_rows_exactly(self):
        orders = [order(f"o{i}", age=8 + i * 3) for i in range(12)] + [order("draft", docstatus=0)]
        code, out, err, rep = run(orders, "--out", "{tmp}")
        self.assertEqual(code, 0)
        rows = rep["sections"]["orders_not_invoiced"]["rows"]
        self.assertEqual(out.splitlines(), [r["id"] for r in rows])
        self.assertEqual(len(rows), 12)
        self.assertIn("wrote ", err)               # the summary line moved to stderr

    def test_no_orders_is_an_empty_list_and_exit_0(self):
        code, out, err, _ = run([order("new", age=1)])
        self.assertEqual((code, out), (0, ""))
        self.assertIn("0 orders not invoiced", err)

    def test_numeric_ids_are_printed_as_text(self):
        code, out, _, _ = run([order(101), order(7, age=20)])
        self.assertEqual((code, out), (0, "7\n101\n"))

    def test_unusable_date_exits_4_after_printing_the_ids(self):
        for bad in ({"date": None}, {"date": "2026-02-30"}, {"date": "2026-12-01"}):
            with self.subTest(bad=bad):
                code, out, err, _ = run([order("ok"), order("bad", **bad)])
                self.assertEqual(code, output.EXIT_INCOMPLETE)
                self.assertEqual(out, "ok\n")
                self.assertIn("may be incomplete: 1 order(s) with an unusable order date left out", err)

    def test_row_without_id_exits_4_and_is_not_printed(self):
        code, out, err, _ = run([order("ok"), order(None, age=9)])
        self.assertEqual((code, out), (4, "ok\n"))
        self.assertIn("1 order(s) without a record id left out", err)

    def test_files_are_written_even_when_the_list_is_incomplete(self):
        code, _, _, rep = run([order("ok"), order("bad", date=None)], "--out", "{tmp}")
        self.assertEqual(code, 4)
        self.assertEqual([r["id"] for r in rep["sections"]["orders_not_invoiced"]["rows"]], ["ok"])

    def test_markdown_flag_prints_the_report_instead(self):
        code, out, _, _ = run([order("a")], "--markdown")
        self.assertEqual(code, 0)
        self.assertIn("## Orders not invoiced (1)", out)
        self.assertNotIn("\na\n", "\n" + out)

    def test_exit_codes_stay_distinct(self):
        # 2 is argparse's bad options; 3 is "platform could not be read" (#54).
        self.assertNotIn(output.EXIT_INCOMPLETE, (0, 1, 2, 3))

    def test_gaps_for_a_section_that_is_not_ok(self):
        rep = {"sections": {"orders_not_invoiced": {"status": "blocked", "rows": []}}}
        self.assertEqual(output.order_list_gaps(rep), ["orders not invoiced is blocked"])


if __name__ == "__main__":
    unittest.main()
