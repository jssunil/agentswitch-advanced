"""Boundary conditions for section 1, the order ID list, --days and AGENT_RESULT (after PR #54's bug hunts).

Each case sits on an edge: N = 0, today and tomorrow, leap days, timestamps either side of midnight, falsy or odd
record ids, the --days / prompt / O2C_DAYS order, and the 4,000-character output tail seat 34 reads.
Seat 34's own recount rule (#55, eval_harness/rules.py) reads a date as `str(value)[:10]`; where it matters, the
expected result here is checked against that reading too, so both sides agree on the edge.
"""
import contextlib
from datetime import date, timedelta
import io
import json
import os
import unittest
from unittest.mock import patch

from o2c import __main__ as cli
from o2c import output
from tests.test_order_ids import D, Orders, order, run, split_result


def seat34_day(value):
    """Seat 34's reading of a date (#55 rules.py `_day`): the first 10 characters, or None."""
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def cli_run(orders, *argv, env=None):
    """(exit code, stdout without AGENT_RESULT, stderr, result) with full control of argv and environment."""
    with patch.dict(os.environ, env or {}, clear=False), \
            patch("sys.argv", ["o2c", "--date", D.isoformat(), *argv]), \
            patch.object(cli, "Client", return_value=Orders(orders)), \
            contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
        try:
            cli.main()
            code = 0
        except SystemExit as e:
            code = e.code
    if code == 2:   # argparse stopped before any output
        return code, out.getvalue(), err.getvalue(), None
    stdout, result = split_result(out.getvalue())
    return code, stdout, err.getvalue(), result


class Dates(unittest.TestCase):
    def test_n_zero_today_is_not_listed_yesterday_is(self):
        code, out, _, _ = cli_run([order("today", age=0), order("yesterday", age=1)], "--days", "0")
        self.assertEqual((code, out), (0, "yesterday\n"))

    def test_tomorrow_is_a_future_date_and_the_list_may_be_incomplete(self):
        code, out, err, _ = run([order("ok"), order("tomorrow", date=(D + timedelta(days=1)).isoformat())])
        self.assertEqual((code, out), (4, "ok\n"))
        self.assertIn("unusable order date", err)

    def test_leap_days(self):
        code, out, _, _ = run([order("leap", date="2024-02-29"), order("not-leap", date="2026-02-29")])
        self.assertEqual((code, out), (4, "leap\n"))   # 2026 has no 29 Feb: invalid, so exit 4
        self.assertIsNone(seat34_day("2026-02-29"))    # seat 34 skips it too

    def test_a_leading_space_makes_a_date_invalid_on_both_sides(self):
        code, out, _, _ = run([order("ok"), order("spaced", date=" 2026-11-01")])
        self.assertEqual((code, out), (4, "ok\n"))
        self.assertIsNone(seat34_day(" 2026-11-01"))

    def test_timestamps_count_the_date_as_written(self):
        # 7 Nov is 8 days before 15 Nov (listed at N = 7); 8 Nov is 7 days (not listed). The offset never moves
        # the date, which is also how seat 34's str[:10] reads it.
        values = {"a": "2026-11-07T23:59:59+05:30", "b": "2026-11-07T23:59:59-08:00", "c": "2026-11-08T00:00:00Z"}
        code, out, _, _ = run([order(k, date=v) for k, v in values.items()])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(out.split()), ["a", "b"])
        self.assertEqual({k for k, v in values.items() if (D - seat34_day(v)).days > 7}, {"a", "b"})

    def test_a_very_old_order_and_a_large_n(self):
        code, out, _, _ = cli_run([order("old", date="1999-01-01"), order("recent", age=3000)], "--days", "3650")
        self.assertEqual((code, out), (0, "old\n"))


class Ids(unittest.TestCase):
    def test_id_zero_is_printed(self):
        code, out, _, _ = run([order(0)])
        self.assertEqual((code, out, run.result["answer"]["orders_not_invoiced"]), (0, "0\n", ["0"]))

    def test_an_empty_or_blank_id_is_not_printed_and_the_list_may_be_incomplete(self):
        for blank in ("", "   "):
            with self.subTest(id=repr(blank)):
                code, out, err, _ = run([order("ok"), order(blank, age=9)])
                self.assertEqual((code, out), (4, "ok\n"))
                self.assertIn("1 order(s) without a record id left out", err)
                self.assertEqual(run.result["answer"]["orders_not_invoiced"], ["ok"])

    def test_a_repeated_id_is_printed_once_and_the_list_may_be_incomplete(self):
        code, out, err, _ = run([order("dup", age=20), order("ok"), order("dup", age=9)])
        self.assertEqual((code, out), (4, "dup\nok\n"))   # first place in report order (oldest first)
        self.assertIn("1 order id(s) repeated in the source", err)
        self.assertEqual(run.result["answer"]["orders_not_invoiced"], ["dup", "ok"])
        self.assertIn("1 order id(s) repeated in the source; printed once", run.result["incomplete"])

    def test_a_non_ascii_id_is_printed_and_the_result_decodes_to_it(self):
        code, out, _, _ = run([order("बिक्री-1")])
        self.assertEqual((code, out), (0, "बिक्री-1\n"))
        self.assertEqual(run.result["answer"]["orders_not_invoiced"], ["बिक्री-1"])


class DaysPrecedence(unittest.TestCase):
    PROMPT = "orders older than 14 days"

    def test_days_zero_beats_the_prompt(self):
        code, out, err, _ = cli_run([order("a", age=5)], "--days", "0", "--prompt", self.PROMPT)
        self.assertEqual(out, "a\n")
        self.assertIn("--days 0 wins over the prompt", err)

    def test_the_same_n_twice_is_not_a_conflict(self):
        _, out, err, _ = cli_run([order("a", age=20)], "--days", "14", "--prompt", self.PROMPT)
        self.assertEqual(out, "a\n")
        self.assertNotIn("wins over", err)

    def test_o2c_days_with_spaces_is_read_and_empty_is_a_usage_error(self):
        code, out, _, _ = cli_run([order("a", age=8)], env={"O2C_DAYS": " 7 "})
        self.assertEqual((code, out), (0, "a\n"))
        code, _, err, _ = cli_run([order("a", age=8)], env={"O2C_DAYS": ""})
        self.assertEqual(code, 2)
        self.assertIn("O2C_DAYS", err)


class ResultEdges(unittest.TestCase):
    def test_refusal_and_an_incomplete_list_together(self):
        code, out, _, result = cli_run([order("ok"), order("bad", date=None)],
                                       "--prompt", "mark these orders as billed")
        self.assertEqual(code, 4)
        self.assertEqual(result["claim"], "refused")
        self.assertEqual(result["answer"]["orders_not_invoiced"], ["ok"])
        self.assertIn("incomplete", result)

    def test_markdown_still_ends_with_the_result(self):
        code, out, _, result = cli_run([order("a"), order("b", age=20)], "--markdown")
        self.assertEqual(code, 0)
        self.assertIn("## Orders not invoiced (2)", out)
        self.assertEqual(result["answer"]["orders_not_invoiced"], ["b", "a"])

    def test_a_prompt_naming_only_unknown_orders_gives_an_empty_answer(self):
        _, out, _, result = cli_run([order("a")], "--prompt", "is SO-404 billed?")
        self.assertEqual((out, result), ("", {"claim": "done", "answer": {"orders_not_invoiced": []}}))

    def test_the_output_tail_holds_exactly_100_ids(self):
        # Seat 34's recount grader (#51) reads AGENT_RESULT from the last 4,000 characters. Pin the limit, so a
        # change to the line's format can't shrink it unnoticed. 36-character ids, as on the platform.
        ids = [f"0b5e2c1a-0000-4000-8000-{n:012d}" for n in range(101)]
        self.assertLessEqual(len(output.agent_result(ids[:100])), 4000)
        self.assertGreater(len(output.agent_result(ids[:101])), 4000)
        gaps = ["1 order(s) with an unusable order date left out"]
        self.assertLessEqual(len(output.agent_result(ids[:99], gaps=gaps)), 4000)
        self.assertGreater(len(output.agent_result(ids[:100], gaps=gaps)), 4000)
        self.assertEqual(json.loads(output.agent_result(ids[:100])[len("AGENT_RESULT "):])["answer"]
                         ["orders_not_invoiced"], ids[:100])


if __name__ == "__main__":
    unittest.main()
