"""The shared deal row (rows.py): nothing guessed, nothing dropped, exact money."""
import json
import unittest
from datetime import date
from decimal import Decimal

from brief.rows import FIELDS, DealRow, from_dict, from_record, replace, stage_labels, to_dict

PIPELINES = [
    {"id": "p1", "key": "sales", "company_id": "c1",
     "stages": [{"key": "new", "label": "New"}, {"key": "proposal", "label": "Quotation sent"},
                {"key": "closed_won", "label": "Won"}]},
    {"id": "p2", "key": "sales", "company_id": "c2", "stages": [{"key": "proposal", "label": "Proposal"}]},
    {"id": "p3", "key": "export", "company_id": "c2", "stages": [{"key": "new", "name": "Fresh"}]},
    {"id": "bad", "stages": "not a list"},
]


def deal(**over):
    record = {"id": "d1", "company_id": "c1", "title": "Brake pads", "party_id": "pa1",
              "_party_id_display": "Acme Ltd", "contact_id": "co1", "owner": "Asha",
              "pipeline": "sales", "stage": "proposal", "value": 1250000.5, "currency": "INR",
              "_base_value": 1250000.5, "_base_currency": "INR", "probability": 40.0,
              "expected_close_date": "2026-10-31", "_rot_days": 66, "_rot_level": "critical",
              "updated_at": "2026-10-24T09:15:00Z", "updated_by": "u1"}
    record.update(over)
    return record


class FromRecord(unittest.TestCase):
    def setUp(self):
        self.labels = stage_labels(PIPELINES)

    def test_clean_deal(self):
        r = from_record(deal(), self.labels)
        self.assertEqual(r.stage_label, "Quotation sent")
        self.assertTrue(r.stage_known)
        self.assertEqual(r.value, Decimal("1250000.5"))
        self.assertIsInstance(r.base_value, Decimal)
        self.assertEqual(r.expected_close_date, date(2026, 10, 31))
        self.assertEqual(r.rot_days, 66)
        self.assertEqual(r.issues, ())
        self.assertFalse(r.fixture_flag)

    def test_pipeline_key_is_resolved_within_the_company(self):
        self.assertEqual(from_record(deal(company_id="c2"), self.labels).stage_label, "Proposal")

    def test_label_falls_back_to_name_and_unique_key(self):
        r = from_record(deal(company_id="c9", pipeline="export", stage="new"), self.labels)
        self.assertEqual(r.stage_label, "Fresh")

    def test_ambiguous_pipeline_key_is_not_guessed(self):
        r = from_record(deal(company_id="c9"), self.labels)
        self.assertFalse(r.stage_known)
        self.assertIsNone(r.stage_label)

    def test_unknown_stage_and_no_pipeline_are_issues_not_errors(self):
        r = from_record(deal(stage="bogus_stage"), self.labels)
        self.assertFalse(r.stage_known)
        self.assertIn("stage: not a stage of the deal's pipeline", r.issues)
        r = from_record(deal(pipeline=None), self.labels)
        self.assertIsNone(r.pipeline)
        self.assertEqual(r.stage, "proposal")
        self.assertFalse(r.stage_known)

    def test_bad_values_become_none_with_a_reason(self):
        r = from_record(deal(value="NaN", _base_value=float("inf"), probability=True,
                             expected_close_date="2026-13-40", _rot_days="66", updated_at=5), self.labels)
        for name in ("value", "base_value", "probability", "expected_close_date", "rot_days", "updated_at"):
            self.assertIsNone(getattr(r, name), name)
            self.assertIn(f"{name}: not readable", r.issues)

    def test_missing_is_none_without_an_issue(self):
        r = from_record(deal(value=None, expected_close_date=None, _rot_days=None), self.labels)
        self.assertIsNone(r.value)
        self.assertEqual(r.issues, ())

    def test_currency_is_kept_as_recorded_unless_malformed(self):
        self.assertEqual(from_record(deal(currency="zzz"), self.labels).currency, "ZZZ")
        r = from_record(deal(currency="RUPEES"), self.labels)
        self.assertIsNone(r.currency)
        self.assertIn("currency: not a three-letter code", r.issues)

    def test_control_characters_are_removed_from_text(self):
        r = from_record(deal(title="Pads\x1b]0;owned\x07", _party_id_display="Acme‮Ltd"), self.labels)
        self.assertEqual(r.title, "Pads]0;owned")
        self.assertEqual(r.account, "AcmeLtd")

    def test_record_without_id_is_refused(self):
        for bad in ({}, {"id": ""}, None, "d1"):
            with self.assertRaises(ValueError):
                from_record(bad, self.labels)

    def test_no_labels_means_no_stage_known(self):
        self.assertFalse(from_record(deal()).stage_known)


class Snapshots(unittest.TestCase):
    def test_round_trip_through_json_is_exact(self):
        r = replace(from_record(deal(value="0.1", _base_value="12345678901234567890.07"),
                                stage_labels(PIPELINES)), fixture_flag=True)
        again = from_dict(json.loads(json.dumps(to_dict(r))))
        self.assertEqual(again, r)
        self.assertEqual(again.base_value, Decimal("12345678901234567890.07"))

    def test_dict_has_exactly_the_frozen_fields(self):
        self.assertEqual(tuple(to_dict(from_record(deal())).keys()), FIELDS)

    def test_frozen_field_list(self):
        # Changing this list needs agreement from both halves (PLAN.md, "Interfaces").
        self.assertEqual(FIELDS, (
            "id", "title", "company_id", "party_id", "account", "contact_id", "owner", "pipeline",
            "stage", "stage_label", "stage_known", "value", "currency", "base_value", "base_currency",
            "probability", "expected_close_date", "rot_days", "rot_level", "updated_at", "updated_by",
            "issues", "fixture_flag"))

    def test_row_is_immutable(self):
        r = from_record(deal())
        with self.assertRaises(Exception):
            r.title = "x"
        self.assertIsInstance(r, DealRow)


if __name__ == "__main__":
    unittest.main()
