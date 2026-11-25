import unittest
from datetime import date

from brief import accounts, tasks_brief
from brief.rows import from_record, stage_labels
from tests.fakes import PIPELINES, FakeClient, deal

AS_OF = date(2026, 11, 30)


def row(i, **over):
    return from_record(deal(i, **over), stage_labels(PIPELINES))


def party(pid, **over):
    record = {"id": pid, "name": f"Account {pid}", "type": "organization", "currency_id": "INR",
              "credit_limit": 50000.0, "addresses": [{"city": "Pune", "state": "Maharashtra"}],
              "_redacted_fields": ["tax_id", "pan"]}
    record.update(over)
    return record


def act(aid, **over):
    record = {"id": aid, "type": "call", "done": True, "cancelled": False, "due_date": "2026-11-20",
              "subject": f"Subject {aid}", "deal_id": None, "party_id": None}
    record.update(over)
    return record


def facts(r, activities=(), parties=(), notes=(), rows=None, relationships=None):
    data = {"parties": {p["id"]: p for p in parties}, "activities": list(activities), "notes": list(notes),
            "relationships": relationships, "rows": rows}
    return accounts.facts(None, r, AS_OF, data=data)


class SourceTest(unittest.TestCase):
    def test_deal_activities_first(self):
        f = facts(row(1), [act("a1", deal_id="d01", party_id="pa1"), act("a2", party_id="pa1")])
        self.assertEqual(f["source"], "deal")
        self.assertEqual(f["last_contact"]["subject"], "Subject a1")

    def test_account_activities_when_the_deal_has_none(self):
        f = facts(row(1), [act("a2", party_id="pa1")])
        self.assertEqual(f["source"], "party")

    def test_another_deals_activities_never_count(self):
        f = facts(row(1), [act("a3", deal_id="d02", party_id="pa1")])
        self.assertIsNone(f["source"])
        self.assertIsNone(f["last_contact"])
        self.assertEqual(f["open_activities"], [])

    def test_cancelled_are_ignored_also_for_the_source(self):
        f = facts(row(1), [act("a1", deal_id="d01", cancelled=True), act("a2", party_id="pa1")])
        self.assertEqual(f["source"], "party")


class LastContactTest(unittest.TestCase):
    def test_latest_done_contact_on_or_before_the_date(self):
        f = facts(row(1), [act("a1", deal_id="d01", due_date="2026-11-01"),
                           act("a2", deal_id="d01", due_date="2026-11-30", type=" Meeting "),
                           act("a3", deal_id="d01", due_date="2026-12-01"),              # after as_of
                           act("a4", deal_id="d01", due_date="2026-11-29", done=False),  # not done
                           act("a5", deal_id="d01", due_date="2026-11-29", type="lunch")])
        self.assertEqual(f["last_contact"], {"date": "2026-11-30", "type": "meeting", "subject": "Subject a2"})

    def test_tie_goes_to_the_highest_id_as_text(self):
        f = facts(row(1), [act("a9", deal_id="d01"), act("a10", deal_id="d01")])
        self.assertEqual(f["last_contact"]["subject"], "Subject a9")

    def test_no_usable_date_does_not_count(self):
        f = facts(row(1), [act("a1", deal_id="d01", due_date="soon"), act("a2", deal_id="d01", due_date=None)])
        self.assertIsNone(f["last_contact"])


class OpenActivitiesTest(unittest.TestCase):
    def test_open_activities_and_overdue(self):
        f = facts(row(1), [act("a1", deal_id="d01", done=False, due_date="2026-11-29"),
                           act("a2", deal_id="d01", done=False, due_date="2026-11-30"),
                           act("a3", deal_id="d01", done=False, due_date="next week"),
                           act("a4", deal_id="d01", done=False, due_date="2026-11-29T23:30:00Z")])
        self.assertEqual([(a["id"], a["due_date"], a["overdue"]) for a in f["open_activities"]],
                         [("a1", "2026-11-29", True), ("a4", "2026-11-29", True),
                          ("a2", "2026-11-30", False), ("a3", None, False)])

    def test_subject_is_one_line(self):
        f = facts(row(1), [act("a1", deal_id="d01", done=False, subject="Call\nback now\x1b[2J")])
        self.assertEqual(f["open_activities"][0]["subject"], "Call back now[2J")


class AccountTest(unittest.TestCase):
    def test_account_fields(self):
        f = facts(row(1), parties=[party("pa1")])
        self.assertEqual({k: f[k] for k in ("found", "name", "type", "city", "state", "currency", "credit_limit")},
                         {"found": True, "name": "Account pa1", "type": "organization", "city": "Pune",
                          "state": "Maharashtra", "currency": "INR", "credit_limit": "50000.0"})
        self.assertEqual(f["redacted"], [])
        self.assertEqual(accounts.issues(row(1), f), [])

    def test_redacted_name(self):
        f = facts(row(1), parties=[party("pa1", _redacted_fields=["name", "credit_limit"])])
        self.assertIsNone(f["name"])
        self.assertIsNone(f["credit_limit"])
        self.assertEqual(f["redacted"], ["credit_limit", "name"])
        self.assertEqual(f["type"], "organization")

    def test_not_found(self):
        f = facts(row(1, party_id="pa404"), [act("a1", deal_id="d01")], parties=[party("pa1")])
        self.assertEqual((f["found"], f["name"], f["type"], f["redacted"]), (False, None, None, []))
        self.assertIsNone(f["contacts"])              # as the other account fields (PLAN.md)
        self.assertEqual(f["source"], "deal")       # the deal's own activities still count
        self.assertEqual(accounts.issues(row(1), f), [{"deal_id": "d01", "issue": accounts.ACCOUNT_NOT_FOUND}])

    def test_no_account_on_the_deal(self):
        f = facts(row(1, party_id=None), [act("a2", party_id="pa1")], parties=[party("pa1")])
        self.assertEqual((f["found"], f["source"]), (False, None))

    def test_name_kept_exactly(self):
        f = facts(row(1), parties=[party("pa1", name="Acme  Ltd")])
        self.assertEqual(f["name"], "Acme  Ltd")


class ContactsNotesDealsTest(unittest.TestCase):
    def test_contacts_deal_contact_first_then_linked_people(self):
        people = [party("co1", name="Asha"), party("p2", name="Ravi"), party("p3", name="Hidden", _redacted_fields=["name"])]
        links = [{"id": "r1", "from_party_id": "p2", "to_party_id": "pa1", "relationship": "represents"},
                 {"id": "r2", "from_party_id": "p3", "to_party_id": "pa1", "relationship": "represents"},
                 {"id": "r3", "from_party_id": "p4", "to_party_id": "pa1", "is_active": False},
                 {"id": "r4", "from_party_id": "co1", "to_party_id": "pa1", "relationship": "represents"}]
        f = facts(row(1), parties=[party("pa1"), *people], relationships=links)
        self.assertEqual(f["contacts"], [{"id": "co1", "name": "Asha", "role": "deal contact"},
                                         {"id": "p2", "name": "Ravi", "role": "represents"},
                                         {"id": "p3", "name": None, "role": "represents"}])

    def test_pinned_note_deal_first_then_account(self):
        notes = [{"id": "n1", "pinned": True, "party_id": "pa1", "title": "Account note", "updated_at": "2026-11-02"},
                 {"id": "n2", "pinned": True, "party_id": "pa1", "deal_id": "d02", "title": "Other deal"},
                 {"id": "n3", "pinned": False, "party_id": "pa1", "deal_id": "d01", "title": "Not pinned"}]
        self.assertEqual(facts(row(1), notes=notes)["pinned_note"], {"id": "n1", "title": "Account note"})
        notes.append({"id": "n4", "pinned": True, "party_id": "pa1", "deal_id": "d01", "title": "Deal note"})
        self.assertEqual(facts(row(1), notes=notes)["pinned_note"], {"id": "n4", "title": "Deal note"})

    def test_notes_not_readable(self):
        f = accounts.facts(None, row(1), AS_OF, data={"parties": {}, "activities": [], "notes": None,
                                                     "relationships": None, "rows": None})
        self.assertIsNone(f["pinned_note"])
        self.assertIsNone(f["other_open_deals"])

    def test_other_open_deals(self):
        rows = [row(1), row(2, party_id="pa1"), row(3, party_id="pa1", stage="closed_won"), row(4)]
        f = facts(rows[0], rows=rows)
        self.assertEqual([d["id"] for d in f["other_open_deals"]], ["d02"])


class LoadTest(unittest.TestCase):
    def test_reads_each_list_once_and_tolerates_missing_lists(self):
        client = FakeClient([deal(1)], parties=[party("pa1")], activities=[act("a1", deal_id="d01")])
        data = accounts.load(client)
        for _ in range(3):
            accounts.facts(client, row(1), AS_OF, data=data)
        self.assertEqual(client.reads, ["Party", "Activity", "Note", "PartyRelationship"])
        self.assertIsNone(data["relationships"])     # the fake has no PartyRelationship list


class MatchesTask7RecountTest(unittest.TestCase):
    def test_same_answers_as_the_task_7_recount(self):
        parties = {p["id"]: p for p in (party("pa1"), party("pa2", _redacted_fields=["name"]))}
        activities = [act(f"a{i}", deal_id=["d01", "d02", None, None][i % 4], party_id=["pa1", "pa2"][i % 2],
                          type=["call", "Email ", "task", "lunch", "MEETING"][i % 5], done=i % 3 != 0,
                          cancelled=i % 7 == 0, due_date=["2026-11-29", "2026-12-02", "bad", None, "2026-11-30"][i % 5])
                      for i in range(40)]
        data = {"parties": parties, "activities": activities, "notes": [], "relationships": [], "rows": None}
        for r in (row(1), row(2, party_id="pa2"), row(3), row(4, party_id="pa404")):
            want = tasks_brief.expected_facts(r, parties, activities, AS_OF)
            got = accounts.facts(None, r, AS_OF, data=data)
            self.assertEqual(tasks_brief._compare_facts(want, got), [], r.id)


if __name__ == "__main__":
    unittest.main()
