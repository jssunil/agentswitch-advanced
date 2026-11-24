"""One suggested next step per deal, from fixed rules, with its reason.

The first rule that matches wins, so the same deal on the same date always gets the same
step, and a test can grade it. The step is only a proposal for a person: this module
never creates an activity or note and never changes a deal (the client cannot write).

Rules, in order (RULES below is the list the tests and PLAN.md refer to):
  1 close_date_passed   expected close date is before the brief's date
  2 overdue_activity    an open activity on the deal or account is past its due date
  3 no_recent_contact   no contact recorded, or the last one is 30 or more days old
  4 fix_record          no close date, a probability of 0, a probability of 100 on a deal that is
                        still open, a probability outside 0-100, or a value the brief could not read
  5 proposal_waiting    in the proposal stage with no movement for 14 or more days. ON HOLD
                        (PROPOSAL_WAITING_ON): the platform's counter may track the deal's age,
                        not its time in the stage (instructor question 15). While on hold the
                        deal falls through to the next rule, so the brief makes no claim about
                        how long it has been in the stage.
  6 negotiation         in the negotiation stage
  7 keep_moving         none of the above

`facts` is accounts.facts() for the deal (Sunil). It may be None or partial: a rule that
needs a missing fact does not match, and nothing is guessed.

Returns {rule, step, reason}; rule 2 also returns `activity` {id, subject, due_date}. The step
and the reason are always our own fixed wording. Record text (the activity's subject) never goes
inside them: report.py shows it separately, quoted and escaped, as CRM text. The one piece of
the deal the reasons name is its stage, and only when it is plain (see _stage_words): a short,
one-line label, or a key of lower-case letters, digits and "_". Anything else is "an
unrecognised stage" (bug hunt BH-01, 24 Nov).
"""
import re
import unicodedata
from datetime import date

from .values import business_date, display_text

RULES = ("close_date_passed", "overdue_activity", "no_recent_contact", "fix_record",
         "proposal_waiting", "negotiation", "keep_moving")
CLOSED = ("closed_won", "closed_lost")
NO_CONTACT_DAYS = 30
PROPOSAL_WAIT_DAYS = 14
PROPOSAL_WAITING_ON = False   # rule 5 on hold until the instructor says what resets the counter
SUBJECT_MAX = 80
STAGE_MAX = 40                     # longest stage label or key the reasons will name
PLAIN_KEY = re.compile(r"[a-z0-9_]{1,%d}" % STAGE_MAX)
OVERDUE_STEP = "Complete the overdue activity"


def suggest(row, facts, as_of):
    """{rule, step, reason} for one open deal on `as_of`."""
    as_of = _day(as_of)
    facts = facts if isinstance(facts, dict) else {}
    stage = _stage_words(row)

    if row.stage in CLOSED:
        return _out("not_open", "No action: the deal is closed", f"Stage is {stage}")

    close = row.expected_close_date
    if close is not None and close < as_of:
        days = (as_of - close).days
        return _out("close_date_passed", "Confirm a new close date with the deal owner",
                    f"Expected close {close.isoformat()} passed {_days(days)} ago; still in {stage}")

    overdue = _overdue_activity(facts.get("open_activities"), as_of)
    if overdue is not None:
        activity_id, subject, due = overdue
        out = _out("overdue_activity", OVERDUE_STEP, f"Due {due.isoformat()}, {_days((as_of - due).days)} overdue")
        out["activity"] = {"id": activity_id, "subject": subject, "due_date": due.isoformat()}
        return out

    if "last_contact" in facts:
        last = facts.get("last_contact")
        last_day = _safe_day(last.get("date")) if isinstance(last, dict) else None
        if last is None:
            return _out("no_recent_contact", "Call or email the main contact",
                        "No contact recorded for this deal or its account")
        if last_day is not None and (as_of - last_day).days >= NO_CONTACT_DAYS:
            return _out("no_recent_contact", "Call or email the main contact",
                        f"Last contact {last_day.isoformat()}, {_days((as_of - last_day).days)} ago")

    missing = []
    if close is None:
        missing.append("no expected close date")
    if row.probability is not None and row.probability == 0:
        missing.append("probability is 0")
    if row.probability is not None and not 0 <= row.probability <= 100:      # bug hunt BH-13
        missing.append("probability is outside 0-100")
    if row.probability is not None and row.probability == 100:
        # a sure win that was never closed (edge case 14); closed deals returned "not_open" above
        missing.append("probability is 100 but the deal is still open")
    # Never true for a deal the brief shows: deals without a readable value are left out of the
    # ranking (edge case 21). Kept as a guard, because suggest() is also called directly.
    if row.value is None or row.base_value is None:
        missing.append("value not readable")
    if missing:
        return _out("fix_record", "Ask the deal owner to update the deal record", "; ".join(missing).capitalize())

    if PROPOSAL_WAITING_ON and row.stage == "proposal" and row.rot_days is not None \
            and row.rot_days >= PROPOSAL_WAIT_DAYS:
        return _out("proposal_waiting", "Follow up on the proposal with the main contact",
                    f"In {stage} with no movement for {_days(row.rot_days)}")

    if row.stage == "negotiation":
        return _out("negotiation", "Agree the final terms and a signing date",
                    f"In {stage}; close expected {close.isoformat()}")

    return _out("keep_moving", "Agree the next meeting with the main contact",
                f"In {stage}; close expected {close.isoformat()}; no rule above applies")


def _overdue_activity(activities, as_of):
    """(id, subject, due date) of the oldest overdue open activity, or None. The id is text, or
    None when the activity has none (never the text "None"). The subject is record text: control
    and invisible characters removed, one line, at most SUBJECT_MAX."""
    oldest = None
    for a in activities or ():
        if not isinstance(a, dict):
            continue
        due = _safe_day(a.get("due_date"))
        if due is None or due >= as_of:
            continue
        # Bug hunt BH-06: every Unicode line break and run of spaces becomes one space (\r, NEL,
        # U+2028/2029 too; display_text keeps them for callers to handle).
        subject = " ".join(display_text(a.get("subject") or "untitled").split()) or "untitled"
        subject = subject if len(subject) <= SUBJECT_MAX else subject[:SUBJECT_MAX - 1] + "…"
        raw_id = a.get("id")
        activity_id = None if raw_id in (None, "") else str(raw_id)
        key = (due, activity_id or "")           # ties: id as text; a missing id sorts first
        if oldest is None or key < oldest[0]:
            oldest = (key, activity_id, subject, due)
    return None if oldest is None else oldest[1:]


def _stage_words(row):
    """How the reasons name the deal's stage. The label (already display_text'd by rows.py) is
    used when it is one line of at most STAGE_MAX characters; otherwise the key, when it is a
    plain key. A deal with no usable pipeline has no label, so its raw key would otherwise go
    into our wording as given (BH-01)."""
    label = row.stage_label.strip() if isinstance(row.stage_label, str) else ""
    if label and len(label) <= STAGE_MAX and not any(
            unicodedata.category(c) in ("Cc", "Zl", "Zp") for c in label):
        return label
    if isinstance(row.stage, str) and PLAIN_KEY.fullmatch(row.stage):
        return row.stage
    return "an unknown stage" if row.stage is None else "an unrecognised stage"


def _out(rule, step, reason):
    return {"rule": rule, "step": step, "reason": reason}


def _days(n):
    return "1 day" if n == 1 else f"{n} days"


def _safe_day(value):
    try:
        return business_date(value)
    except (ValueError, TypeError):
        return None


def _day(value):
    if isinstance(value, date):
        return business_date(value)
    day = business_date(value)
    if day is None:
        raise ValueError("a date is needed")
    return day
