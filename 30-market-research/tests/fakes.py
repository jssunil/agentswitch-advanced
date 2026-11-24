"""Made-up CRM data and a fake read-only client for offline tests. No platform data."""
import copy

PIPELINES = [{"id": "p1", "key": "sales", "company_id": "c1",
              "stages": [{"key": k, "label": k.title()} for k in
                         ("new", "qualification", "proposal", "negotiation", "closed_won", "closed_lost")]}]


def deal(i, **over):
    record = {"id": f"d{i:02d}", "company_id": "c1", "title": f"Deal {i}", "party_id": f"pa{i}",
              "_party_id_display": f"Account {i}", "contact_id": f"co{i}", "owner": "Owner",
              "pipeline": "sales", "stage": "proposal", "value": 1000.0 * i + 0.5, "currency": "INR",
              "_base_value": 1000.0 * i + 0.5, "_base_currency": "INR", "probability": 40.0,
              "expected_close_date": "2026-12-15", "_rot_days": i, "_rot_level": "warm",
              "updated_at": "2026-10-24T09:15:00Z", "updated_by": "u1"}
    record.update(over)
    return record


class FakeClient:
    """list_all() over canned records; `script` lets a test change the data between reads."""

    def __init__(self, deals, pipelines=PIPELINES, activities=(), notes=(), parties=(), kbnotes=()):
        self.deals, self.pipelines, self.reads = deals, pipelines, []
        self.activities, self.notes, self.parties = list(activities), list(notes), list(parties)
        self.kbnotes = list(kbnotes)
        self.on_read = None

    def list_all(self, entity, filters=None):
        self.reads.append(entity)
        if self.on_read:
            self.on_read(self, entity)
        return copy.deepcopy({"Deal": self.deals, "Pipeline": self.pipelines, "Activity": self.activities,
                              "Note": self.notes, "Party": self.parties, "KBNote": self.kbnotes}[entity])

    def call(self, name, args=None):
        from brief.client import WriteBlocked, is_read_tool
        if not is_read_tool(name):
            raise WriteBlocked(f"{name} is not a read tool")   # as the real client does
        raise AssertionError(f"tasks must read through list_all, not {name}")
