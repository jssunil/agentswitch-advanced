"""Seat 34's 30 Nov recount, done independently on hundreds of random order sets.

The recount (phase 2 item 1) compares the ids we print with the platform's own records. Here a short, separate
implementation of the section 1 rule plays the platform side:

    an order is listed when it is submitted (docstatus 1), not fully invoiced (unbilled or partially_invoiced),
    and its order date is a real date, not after the report date, and more than N days before it.

An order of that kind whose date is missing, invalid or in the future can't be aged, so the list may be
incomplete: exit 4. Each scenario is seeded, so a failure names a scenario that can be replayed.
"""
import contextlib
from datetime import date, timedelta
import io
import random
import unittest
from unittest.mock import patch

from o2c import __main__ as cli

D = date(2026, 11, 15)
OPEN = ("unbilled", "partially_invoiced")
SCENARIOS = 300


class Orders:
    def __init__(self, orders):
        self.orders = orders

    def tool_names(self):
        return {"SalesOrder.list"}

    def list_all(self, entity, filters=None):
        rows = self.orders if entity == "SalesOrder" else []
        return [r for r in rows if all(r.get(k) == v for k, v in (filters or {}).items())]


def random_orders(rng, days):
    orders = []
    for i in range(rng.randint(0, 40)):
        age = rng.choice([days - 1, days, days + 1, days + 2, rng.randint(0, 400)])
        day = D - timedelta(days=age)
        kind = rng.choices(["date", "timestamp", "missing", "empty", "invalid", "garbage", "future"],
                           weights=[70, 10, 4, 4, 4, 4, 4])[0]
        value = {"date": day.isoformat(), "timestamp": f"{day.isoformat()}T12:00:00Z", "missing": None, "empty": "",
                 "invalid": "2026-02-30", "garbage": "soon", "future": (D + timedelta(days=rng.randint(1, 30))).isoformat()}[kind]
        orders.append({"id": f"o-{i:03d}", "number": f"SO-{i:05d}", "date": value,
                       "docstatus": rng.choices([1, 0, 2], weights=[80, 10, 10])[0],
                       "invoiced_status": rng.choice(OPEN + ("fully_invoiced", None)),
                       "grand_total": rng.choice([100, 2500.5, 0]), "currency_code": "INR",
                       "_party_id_display": f"Customer {rng.randint(1, 5)}", "_age": age if kind in ("date", "timestamp") and age >= 0 else None})   # age -1 is a future date
    return orders


def recount(orders, days):
    """(expected ids, list may be incomplete): the section 1 rule, written from scratch."""
    candidates = [o for o in orders if o["docstatus"] == 1 and o["invoiced_status"] in OPEN]
    ids = {o["id"] for o in candidates if o["_age"] is not None and o["_age"] > days}
    undated = any(o["_age"] is None for o in candidates)   # missing, invalid, unparseable or future
    return ids, undated


def run_cli(orders, days):
    argv = ["o2c", "--date", D.isoformat(), "--days", str(days), "--instance", "synthetic"]
    with patch("sys.argv", argv), patch.object(cli, "Client", return_value=Orders(orders)), \
            contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
        try:
            cli.main()
            code = 0
        except SystemExit as e:
            code = e.code
    return code, out.getvalue().splitlines()


class RecountProperty(unittest.TestCase):
    def test_printed_ids_equal_an_independent_recount(self):
        for seed in range(SCENARIOS):
            rng = random.Random(seed)
            days = rng.choice([0, 1, 7, 14, 30, 90])
            orders = random_orders(rng, days)
            with self.subTest(seed=seed, days=days, orders=len(orders)):
                expected, undated = recount(orders, days)
                code, printed = run_cli(orders, days)
                self.assertEqual(len(printed), len(set(printed)), "an id is printed twice")
                self.assertEqual(set(printed), expected)
                self.assertEqual(code, 4 if undated else 0)
                ages = [next(o["_age"] for o in orders if o["id"] == i) for i in printed]
                self.assertEqual(ages, sorted(ages, reverse=True), "not oldest first")

    def test_the_boundary_is_strictly_more_than_n_days(self):
        for days in (0, 1, 7, 30):
            with self.subTest(days=days):
                orders = [{"id": f"age-{a}", "number": f"SO-{a}", "date": (D - timedelta(days=a)).isoformat(),
                           "docstatus": 1, "invoiced_status": "unbilled", "grand_total": 1, "currency_code": "INR"}
                          for a in (days - 1, days, days + 1) if a >= 0]
                self.assertEqual(run_cli(orders, days), (0, [f"age-{days + 1}"]))


if __name__ == "__main__":
    unittest.main()
