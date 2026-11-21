"""Tests for the unified seat 31 suite runner (o2c/suite.py)."""
import contextlib
from datetime import date
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from o2c import suite, tasks_orders, tasks_payments
from tests.test_agent_hook import contract_env
from tests.test_harness_contract import FakePlatform, order as hc_order
from tests.test_tasks_payments import FakePlatform as PayFakePlatform, inv, pay

D = date(2026, 11, 20)


class SuiteDiscoveryAndFormat(unittest.TestCase):
    def test_all_tasks_combines_both_modules_dynamically_with_unique_ids(self):
        ids = [t[0] for t in suite.all_tasks()]
        expected = [t[0] for t in tasks_orders.TASKS] + [t[0] for t in tasks_payments.TASKS]
        self.assertEqual(sorted(ids), sorted(expected))
        self.assertEqual(len(set(ids)), len(ids))

    def test_tasks_run_in_suite_number_order(self):
        # Deepak's 21 Nov review: results came out 1-4, 11, 5-10, 12.
        self.assertEqual([t[0] for t in suite.all_tasks()], list(suite.NUMBERED))

    def test_every_numbered_task_exists(self):
        found = {t[0] for t in tasks_orders.TASKS} | {t[0] for t in tasks_payments.TASKS}
        self.assertEqual(set(suite.NUMBERED) - found, set())

    def test_extra_task_in_either_module_is_included_without_fixed_count(self):
        extra = ("extra_check", "Extra check", lambda c: {"passed": True, "score": 1.0, "evidence": "ok"})
        for module in (tasks_orders, tasks_payments):
            with self.subTest(module=module.__name__), mock.patch.object(module, "TASKS", [extra] + module.TASKS):
                ids = [t[0] for t in suite.all_tasks()]
                self.assertEqual(ids[-1], "extra_check")   # unnumbered: after the 12, never dropped
                self.assertEqual(ids[:-1], list(suite.NUMBERED))

    def test_run_suite_writes_task_format_schema_and_handles_crashes(self):
        fake = PayFakePlatform({"SalesOrder": [], "Invoice": [], "Payment": [], "EmailMessage": []})
        ctx = tasks_payments.Ctx(fake, "suryodaya", D)
        tasks = [
            ("ok_task", "Passes", lambda c: {"passed": True, "score": 1.0, "evidence": "verifier=test"}),
            ("boom", "Crashes", lambda c: 1 / 0),
        ]
        payload = suite.run_suite(ctx, tasks)
        self.assertEqual(payload["summary"], "suryodaya: 1/2 tasks passed")
        self.assertEqual([t["id"] for t in payload["tasks"]], ["suryodaya:ok_task", "suryodaya:boom"])
        for entry in payload["tasks"]:
            self.assertEqual(set(entry), {"id", "title", "passed", "score", "evidence"})
        self.assertFalse(payload["tasks"][1]["passed"])
        self.assertEqual(payload["tasks"][1]["score"], 0.0)
        self.assertIn("outcome=infrastructure", payload["tasks"][1]["evidence"])


class SuiteCli(unittest.TestCase):
    def test_cli_writes_results_json_across_all_12_tasks_with_real_agent(self):
        orders = [hc_order(n) for n in range(1, 3)]
        messages = [{"id": "m1", "folder": "drafts", "is_draft": True, "to": "ap@x.example", "subject": "s"}]
        platform = FakePlatform(
            {
                "SalesOrder": orders,
                "Invoice": [inv("I1", "2026-11-01", 10.0)],
                "Payment": [pay("P1", 10.0)],
                "EmailMessage": messages,
                "Party": [{"id": "P1", "name": "Acme", "currency_code": "INR"}],
                "Company": [{"id": "C1", "currency_code": "INR"}],
                "EmailMailbox": [{"id": "MB", "email": "me@x.example", "company_id": "C1",
                                  "user_id": "U-31", "is_active": True}],
            },
            tools=sorted({
                "SalesOrder.list", "Invoice.list", "Payment.list", "Party.list", "Company.list",
                "EmailMessage.list", "EmailMailbox.list", "SalesOrder.update", "EmailMessage.send_email",
            }),
        )
        self.addCleanup(platform.close)

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "nested" / "results.json"
            env = contract_env(platform, AGENTSWITCH_RUN_ID="run-1")
            argv = ["o2c.suite", "--date", D.isoformat(), "--agent", "--out", str(out_path)]
            with mock.patch.dict(os.environ, env, clear=False), \
                    mock.patch("sys.argv", argv), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                suite.main()

            self.assertTrue(out_path.exists())
            data = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["tasks"]), len(suite.all_tasks()))
            self.assertIn("synthetic:", data["summary"])
            self.assertIn(data["summary"], out.getvalue())
            by_id = {t["id"]: t for t in data["tasks"]}
            self.assertTrue(by_id["synthetic:refuse_send"]["passed"], by_id["synthetic:refuse_send"]["evidence"])
            self.assertTrue(by_id["synthetic:refuse_edit_foreign_order"]["passed"],
                            by_id["synthetic:refuse_edit_foreign_order"]["evidence"])

    def test_cli_defaults_date_to_today_ist_and_rejects_bad_days(self):
        fake = PayFakePlatform({"SalesOrder": [], "Invoice": [], "Payment": [], "EmailMessage": []})
        with mock.patch.dict(os.environ, {"AGENTSWITCH_INSTANCE": "keystone"}, clear=False), \
                mock.patch("sys.argv", ["o2c.suite"]), \
                mock.patch.object(suite, "Client", return_value=fake), \
                mock.patch.object(suite.report, "today_ist", return_value=D), \
                mock.patch.object(suite, "run_suite", return_value={"tasks": [], "summary": "keystone: 0/0 tasks passed"}) as rs, \
                contextlib.redirect_stdout(io.StringIO()):
            suite.main()
        self.assertEqual(rs.call_args.args[0].report_date, D)

        with mock.patch("sys.argv", ["o2c.suite", "--instance", "keystone", "--days", "-1"]), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                suite.main()
        self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
