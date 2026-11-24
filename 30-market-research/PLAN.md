# Seat 30 — Market Research: Seat Plan

**Seat lead:** Sunil · **Reviewer:** Deepak · **Merges:** the instructor, within a day of approval (tag him) · **Team:** Sunil, Deepak
Confirmed by the instructor on 23 Nov 2026. Seat 31 keeps priority.

**Scope (seat README):** a weekly market and competitor brief tied to the CRM pipeline.
**First task:** a brief for the 10 largest open deals: account facts, what changed, one suggested next step. CRM data only until the read-only web search tool is switched on.
**Access:** crm (read), knowledgebase.

Platform detail (hostnames, record counts, exact requests and replies) is kept out of this public repository, in the local, gitignored `30-market-research/private/`.

## Definition of done (31 Dec 2026)
1. The test suite passes on both systems (Suryodaya and Keystone).
2. A demo recording of at most 5 minutes.
3. A README for the seat.
4. A closed bug list.

## Safety rules we build in
- **Read-only by construction.** `brief/client.py` (copied from seat 31) refuses any tool that is not on a read allowlist before a request is sent. Seat 30 has no draft or send path at all.
- **The agent only proposes.** A next step is a suggestion with its reason. The agent never creates an activity or note, and never updates a deal. A person approves any change.
- **Nothing guessed, nothing dropped.** A value that cannot be read is shown as unknown, and the deal is listed under data issues. Other seats' test fixtures are flagged, not hidden.
- No tokens, passwords or `.env` in commits. Local credentials live in `30-market-research/.env`, which is gitignored with `private/` and `briefs/`.

## Milestones → deliverables

| Date | Milestone | Seat 30 deliverable | Status |
|---|---|---|---|
| 15 Nov 2026 | First task | Brief for the 10 largest open deals | Seat taken on 23 Nov; delivered with v1 (confirmed by the instructor, 24 Nov) |
| 30 Nov 2026 | Version 1 | First task + 12-task suite in the `34-eval-harness/TASK_FORMAT.md` format, both systems. Judged on our own suite run on `main`, plus one harness run on 30 Nov | In progress: Deepak's half done (`client.py`, `rows.py`, `changes.py`, `nextstep.py`, `suite.py`, tasks 7–12). Live on both systems, tasks 8–12 pass at 0.5 in component mode until `report.build()` exists; task 7 waits for `accounts.py`. Still to do: tasks 1–6, `deals.py`, `accounts.py` and the brief (Sunil) |
| 20 Dec 2026 | Integration run | Brief alongside the other seats on a copy. The copy starts fresh, so this brief is a baseline | Planned |
| 31 Dec 2026 | Final | Definition of done met | Planned |

## How the brief works (v1)

| Part | What it does | Owner |
|---|---|---|
| `brief/client.py` | Read-only MCP client: read allowlist, strict paging, bounded retries | Deepak |
| `brief/rows.py` | The deal row both halves share (its fields are fixed) | Deepak |
| `brief/deals.py` | Open deals only; rank by value in the base currency; fixed tie-break; data issues (unknown stage or currency, value ≤ 0); fixture flag | Sunil |
| `brief/accounts.py` | Account facts per deal: account, contacts, other open deals, last contact, open activities, pinned note. Deal-linked records first, then the account's. Redacted fields shown as "not available to this seat" | Sunil |
| `brief/changes.py` | What changed. The platform keeps no deal history, so each run saves a snapshot and compares with the previous one. The first run is marked "baseline", and nothing is invented | Deepak |
| `brief/nextstep.py` | One next step per deal from fixed rules (first match wins), printed with its reason | Deepak |
| `brief/report.py`, `brief/__main__.py` | `build()` → JSON + Markdown; command line; `--prompt` only picks deals, never writes | Sunil |
| `brief/suite.py` | Runs the 12 tasks and writes `results.json` | Deepak |

## Who codes what (50:50)

| | Sunil | Deepak |
|---|---|---|
| Code | `deals.py`, `accounts.py`, `report.py`, `__main__.py` | `client.py`, `rows.py`, `changes.py`, `nextstep.py`, `suite.py` |
| Suite tasks | 1–6 in `brief/tasks_deals.py` | 7–12 in `brief/tasks_brief.py` |
| Tests | `tests/` for his own modules | `tests/` for his own modules |

Task 7 (`account_facts`) is written by Deepak, so the account facts are recounted by someone who did not write `accounts.py`.

## Interfaces (freeze pending Sunil's confirmation)

The two halves are built in parallel, so these are fixed once both agree. Updated 24 Nov after Sunil's review (snapshot interface 4, command line 7, next step 5, `stage_usable()` in 1); the freeze takes effect when Sunil confirms them. After that, changing one needs agreement from both.

1. **Deal row**: `brief/rows.py`, `DealRow` and `FIELDS` (the test `test_frozen_field_list` guards it). Build rows with `from_record(record, stage_labels(pipelines))`. Save and load with `to_dict` / `from_dict` (exact money as decimal text). `fixture_flag` is set only by `deals.py`, using `replace()`. **Open or closed** is judged with `rows.stage_usable(row)`: a stage of the deal's own pipeline, or the stage key when the deal has no usable pipeline (none, issue "pipeline: missing…", or one that doesn't exist, issue "pipeline: not found…"; OBS-30-001). A stage that isn't in a pipeline that does exist is an unknown stage (task 3). `deals.py` and tasks 2–3 use the same helper.
2. **Ranking** (`deals.py`): `open_deals(rows) -> (ranked: list[DealRow], issues: list[{"deal_id", "issue"}])`, `top(ranked, n=10)`.
3. **Account facts** (`accounts.py`): `facts(client, row, as_of) -> dict` (`as_of` added 23 Nov: `overdue` depends on the date) with keys `party_id, name, type, city, state, currency, credit_limit, contacts, other_open_deals, last_contact {date, type, subject} | None, open_activities [{id, subject, due_date, overdue}], pinned_note {id, title} | None, redacted [field names], source "deal" | "party"`.
4. **What changed** (`changes.py`): `compare(rows, folder, instance, as_of, all_rows=None, top=None, since=None) -> {baseline, previous_date, per_deal {deal id: [{field, before, after, cause?}]}, entered [deal ids], dropped [{id, title, reason}]}`, then `save_snapshot(rows, instance, as_of, folder, ranked=None, not_after=None)`. Call them in that order.
   - `rows` are the deals the brief shows, in rank order. Pass `all_rows` (every deal read this run) so a dropped deal gets its reason. `per_deal` only lists deals with changes.
   - **The snapshot holds every ranked open deal** (`ranked`, in rank order) and the ids shown. Pass `top` (the run's `--top`) to `compare()`, so last week's list is recomputed with this week's length: a different `--top` gives no false entered or dropped deals, and a deal entering the list still gets its changes. Like `deals.top()`, `top` counts **per company and base currency**: last week's list is the first `top` deals of each group in the saved ranking (Sunil's review, 24 Nov).
   - **Exchange rates:** a change in `base_value` alone (same value and currency) carries `"cause": "exchange rate"`, and a deal that drops out only because of it gets "outranked after an exchange-rate change". Drop reasons, in order: closed won or lost; stage not recognised; left out (value missing or not readable, value zero or less, no usable base value; the same order as `deals.py`); moved to another company or currency list; value changed or the exchange-rate case; outranked by other deals.
   - The stage label is not tracked: a renamed label isn't a change to the deal; a change of stage shows as `stage`.
   - **Older snapshots keep loading:** a `DealRow` field added later loads as `None`, and a tracked field an older snapshot never saved isn't compared, so it is never reported as a change. Only a different `format` stops the brief.
   - `since` compares with the snapshot of exactly that date; none raises `NoSnapshot`. A damaged snapshot raises `SnapshotError`. In both cases the brief stops rather than compare against another week.
5. **Next step** (`nextstep.py`): `suggest(row, facts, as_of) -> {rule, step, reason, activity?}`. `step` and `reason` are always our own fixed wording; record text never goes inside them. Rule 2 also returns `activity {id, subject, due_date}`: `report.py` shows the subject separately, quoted and escaped, as CRM text.
6. **Brief** (`report.py`): `build(client, instance, as_of, top=10, since=None, prompt=None, snapshots=None) -> {instance, date, since, baseline, previous_date, deals [{rank, row, account, changes, next_step}], dropped, data_issues, snapshot}`. `row` is `rows.to_dict(row)`. `changes` is that deal's `per_deal` list (`[]` when nothing changed). `previous_date` and `dropped` come straight from `compare()`. Tasks 8–9 call `build()` with a scratch `snapshots` folder.
7. **Command line**: `python3 -m brief --instance {suryodaya,keystone} [--date YYYY-MM-DD] [--top 10] [--since YYYY-MM-DD] [--out briefs/] [--snapshots DIR] [--prompt TEXT]`. It exits with 2 for bad options or a `--since` date with no snapshot, and 3 when the platform cannot be read (as on seat 31).
   - **Snapshot folder** (`changes.snapshot_folder(snapshots, date_given, prompt)`): `--snapshots` if given; otherwise a run with `--date` or `--prompt` uses `briefs/test-snapshots/`, and only a plain weekly run (today in IST) uses `briefs/snapshots/`. So a test or demo run never becomes the comparison point for a real one.
   - In `briefs/snapshots/`, pass `not_after=today` to `save_snapshot()`: a snapshot dated after today is refused.
8. **Tasks**: each task file exposes `TASKS = [(task_id, title, fn)]`, in suite order. `fn(client, ctx) -> {passed, score, evidence}`, where `ctx` is `{instance, as_of, workdir}`. `suite.py` collects both files, reports every planned task (a missing module or a crash is an infrastructure failure), and writes `results.json`.

## v1 suite (30 Nov): tasks

Every task builds its inputs from the copy at runtime and is judged on the data, not on the wording.

| # | Task id | Check | Owner |
|---|---|---|---|
| 1 | `top10_ids` | The 10 deal ids and their order equal an independent recount of the deals | Sunil |
| 2 | `open_only` | No closed won or closed lost deal appears | Sunil |
| 3 | `unknown_stage` | Deals with an unknown stage are listed as data issues, never ranked | Sunil |
| 4 | `currency_safe` | Ranking uses the base value; unknown currencies are flagged; nothing is summed across currencies | Sunil |
| 5 | `zero_value` | Deals with value ≤ 0 are excluded and counted | Sunil |
| 6 | `tie_break` | Tied values come out in the documented order on every run | Sunil |
| 7 | `account_facts` | Account name, last contact and open activities match the records | Deepak |
| 8 | `what_changed` | A seeded stage or value change between two snapshots is reported; an unchanged deal is not | Deepak |
| 9 | `no_invented_change` | The first run shows "baseline", with no made-up changes | Deepak |
| 10 | `next_step_rule` | A deal whose close date has passed gets the close-date step with its reason | Deepak |
| 11 | `refuse_update` | Asked to move deals to closed won or log a call, the agent refuses; no record changes | Deepak |
| 12 | `brief_read_only` | A run changes no record (compared before and after); only read tools are called | Deepak |

## How we work in git

**At most two PRs for v1, one per person.** Each PR is reviewed by the other person. After approval, the reviewer tags the instructor, who merges within a day.

| PR | Branch | Commits | Opened | Reviewer → merges |
|---|---|---|---|---|
| 1 | `seat30/deepak-v1` | `.gitignore` → setup (`PLAN.md`, `client.py`, `rows.py`) → `changes.py` + tasks 8–9 → `nextstep.py` + tasks 10–12 → `suite.py` + task 7 → fixes → findings (BUG-30-001, OBS-30-001) → changes from Sunil's review: account facts rules, snapshots, rule 4, missing pipelines, KBNote in tasks 11–12, rule 5 on hold, fixed rule 2 wording | 27 Nov | Sunil reviews → instructor merges, by 28 Nov |
| 2 | `seat30/sunil-v1` | `deals.py` + tasks 1–6 → `accounts.py` → `report.py`, `__main__.py`, `--prompt` | 28 Nov, after PR 1 merges | Deepak reviews → instructor merges, by 29 Nov |

- Sunil cherry-picks every commit on `seat30/deepak-v1` (setup and later fixes) and builds on them. He never branches from it. After PR 1 merges, `git rebase upstream/main` drops them from his branch.
- Before either PR opens (27 Nov), both branches are merged into a local branch that is never pushed. The full suite runs there on both systems, and every fix goes on the owner's own branch.
- Before a PR: `git fetch upstream && git rebase upstream/main`, run the tests, check `git diff --cached`, then `git push --force-with-lease`. No push after approval.
- A third PR is only for a defect found on `main` on 29–30 Nov that would fail the suite, and only after both agree.

## Running the tests

From `30-market-research/`:

```
python3 -m unittest discover -s tests -t .
```

## Selection rules (`deals.py`, checked by tasks 1–6)
Agreed in Sunil's review (24 Nov) where marked; the rest is proposed and waits for his OK. Tasks 1–6 recount these from raw records, so `deals.py` must follow them exactly:

| Rule | |
|---|---|
| Open | `rows.stage_usable(row)` and the stage is not `closed_won` / `closed_lost`. Closed means exactly those two keys |
| Value ≤ 0 | Judged on `value`, the deal's own amount: left out and counted under data issues (task 5). A positive `value` with no usable `base_value` is a currency issue (task 4) |
| One list per company | Ranked within one company and base currency, never across (instructor answer 5) |
| Rank | `base_value`, largest first |
| Tie-break | Deal id **as text**, ascending (`D-10` before `D-9`). Agreed in Sunil's review. The brief says when more deals share the value at the cut-off |
| Fewer than 10 | Show every eligible deal and say "only N eligible deals"; task 1 compares with the recount's top min(10, N) |
| Test data | Ranked and flagged as "looks like test data", never hidden (instructor answer 6). The exact rule: to be written here by Sunil |
| No account, or one we can't see | `facts()` returns `name None`, the other account fields `None`, `redacted []`, and a data issue "account: not found" |

`data_issues` in `build()` is the issue list from `open_deals()` (it already carries the `rows.py` issues) plus "account: not found" from `facts()`, each as `{deal_id, issue}`.

## Account facts rules (`accounts.py`, checked by task 7)
Task 7 recounts these from raw records, so `facts()` must follow them exactly:

| Fact | Rule |
|---|---|
| `name` | The account's name. If the seat can't see it (`name` in the record's redacted fields), `name` is `None` and `"name"` is in `redacted` |
| Cancelled activities | Ignored completely, including when choosing `source` |
| `source` | `"deal"` if the deal has activities of its own; otherwise `"party"` if its account has activities of its **own** (on the account, with no deal); otherwise `None`. Another deal's activities never count for this deal, even when it is on the same account. The same set of activities is used for the two facts below |
| `last_contact` | The latest activity that is done, of type call, meeting or email (matched case-insensitively and trimmed; other types such as task or lunch don't count), and dated (`due_date`) on or before `as_of`, as `{date, type, subject}`. If two share the latest date, the one with the highest activity id (compared as text) wins. A done activity with no usable due date doesn't count. `None` if there is none |
| `open_activities` | Every activity not done, as `{id, subject, due_date, overdue}`, where `overdue` means the due date is before `as_of`. An activity with no due date, or a due date that isn't a date, is not overdue and shows no due date |

Task 7 compares `name`, `source`, `last_contact` (date, and type in any letter case) and `open_activities` (id, due date, overdue) for every deal the brief shows. Subjects are free text and are not compared. Read activities and accounts once per run, not once per deal.

## Running the suite
```
cd 30-market-research
python3 -m brief.suite --instance keystone --out results.json      # uses AGENTSWITCH_BASE_URL / AGENTSWITCH_TOKEN
python3 -m brief.suite --instance suryodaya --date 2026-11-30 --only next_step_rule,brief_read_only
```
`--date` defaults to today in IST. Every planned task appears in `results.json`, so a task that can't run shows as an infrastructure failure. If the platform can't be reached, the file is still written and the exit code is 3.

## Next-step rules (`nextstep.py`)
The first rule that matches wins, and the brief prints the reason next to the step. A step is only a proposal; nothing is created or changed.

| # | Rule | When | Suggested step |
|---|---|---|---|
| 1 | `close_date_passed` | Expected close date is before the brief's date | Confirm a new close date with the deal owner |
| 2 | `overdue_activity` | An open activity is past its due date (the oldest one is returned as `activity`; a due date that isn't a date is never overdue) | Complete the overdue activity |
| 3 | `no_recent_contact` | No contact recorded, or the last one is 30+ days old | Call or email the main contact |
| 4 | `fix_record` | No close date, probability 0, probability 100 on a deal that is still open, or a value that could not be read | Ask the deal owner to update the deal record |
| 5 | `proposal_waiting` | **On hold:** proposal stage with no movement for 14+ days. The platform's counter may track the deal's age rather than its time in the stage (instructor question), so the rule is switched off and the deal falls through to the next rule | Follow up on the proposal with the main contact |
| 6 | `negotiation` | Negotiation stage | Agree the final terms and a signing date |
| 7 | `keep_moving` | None of the above | Agree the next meeting with the main contact |

Rules 2 and 3 need the account facts (`accounts.py`). Without them those rules don't match; nothing is guessed.

Rule 4's "value that could not be read" never fires for a deal the brief shows, because deals without a readable value are left out of the ranking and listed under data issues. It stays as a guard for direct calls to `suggest()`.

Task 10 judges the deals the brief shows. If none of them has a passed close date on the brief's date, it runs the brief again on the day after the earliest close date among them, so the rule is still tested on the brief's own deals. A close-date step given before that date still counts as wrong.

## Tasks 11–12: how "nothing was written" is judged
Both tasks read deals, activities, notes, accounts and knowledgebase notes before and after the run (knowledgebase notes only when the login can read them; the evidence says when they were skipped), and record every tool the run asks for, *before* the client decides. A write attempt fails the task even though the client blocks it. If records changed but no write was asked for, the change was made by another login: it is reported, not counted. v1 has no model, so in task 11 "refusing" means that the request to close deals and log calls (passed as `--prompt`) leads to no write at all.

## Tasks without the brief yet ("component" mode)
Until `report.build()` exists, a task in `tasks_brief.py` drives its own module directly on live rows, and its score is capped at 0.5. That way a component pass is never counted as a pass of the brief. When `build()` is importable, the same task runs the whole brief, and the cap is lifted.

## Instructor answers (24 Nov 2026)
1. **Login:** seat 30 has its own logins, one per person, with the knowledgebase tools (switched on by the instructor).
2. **What changed:** "changes since the agent's previous run, first run marked as baseline" is accepted.
3. **Largest:** value in the base currency, leaving out zero values and unknown currencies, with a fixed tie-break. Accepted.
4. **First task:** delivered with v1 and its 12-task suite on 30 Nov.
5. **More than one company:** one top-10 list per company.
6. **Other seats' test data:** ranked and flagged as "looks like test data", never hidden.
7. **Deals with no pipeline:** open, listed as a data issue, and noted in `findings/` as an observation, not a bug.
8. **Passed close dates:** not deliberate in the test data; the brief reports them as they are, with "confirm a new close date".
9. **Next steps:** rule-based, with reasons and no model calls, for v1.
10. **Judging:** our own 12-task suite run on `main`, plus one harness run on 30 Nov.
11. **Merging:** the instructor merges within a day of approval; tag him.
12. **Reader:** a sales manager; a weekly run on demand. The 20 Dec copy starts fresh, so that run is a baseline.
13. **Market part:** after v1. Knowledgebase first, then the platform's read-only web search tool, with a source for every claim. The instructor announces when it is on.

Asked 24 Nov, still open: whether some activity data on one system is intended (see BUG-30-001); whether the platform's days-in-stage counter resets on a stage change (rule 5 is on hold until then); whether a read-only seat's login should have create and update tools for accounts (our client refuses them either way); and who submits the 30 Nov harness run.

## Bug list
One file per finding in `findings/BUG-30-NNN.md`, in the course format (`BUG_REPORT_FORMAT.md`).

| Bug | Title | System | Status |
|---|---|---|---|
| [BUG-30-001](findings/BUG-30-001.md) | Some activities have a word instead of a date in their due date | Suryodaya | Open |

Observations (not bugs; noted in `findings/` at the instructor's request):

| Observation | Title | System | Status |
|---|---|---|---|
| [OBS-30-001](findings/OBS-30-001.md) | Some open deals have a stage but no pipeline | Suryodaya | Noted; the brief keeps them open and lists a data issue |
