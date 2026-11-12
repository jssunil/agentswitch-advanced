# Section 1/report follow-up

## Scope

This is Sunil's deterministic report change. The agent entry point, model integration, graded
task prompts, canonical payment-source changes and Deepak's draft/refusal verifier remain separate.

Implemented:

1. Preserve submitted/unbilled-or-partly-invoiced eligibility and strict order age > N.
2. Record `age_basis=order_date`, `deliverability_status=unverified`, and age bucket per row.
3. Count buckets on canonical exception rows; no duplicate unverified financial list.
4. Keep global oldest-first sorting. Equal ages group by currency and sort value descending within
   each group; missing/nonfinite/nonnumeric amounts sort after known values, then number/ID.
5. Resolve existing currency provenance before the final sort in build().
6. Surface unusable order dates separately; don't turn malformed dates into zero-day or old orders.
7. Validate CLI and library thresholds; retain explicit dates, UTF-8, escaping and blocked status.

The lack of a verified deliverable-date field is intentional and visible. This change does not
claim to implement deliverability-based ageing. Once the field and policy are agreed, introduce
an explicit new mode/migration with its own fixtures rather than silently changing historical reports.

## Requirements considered

- Seat 31 Admin answers, 10 November: oldest first/value ties, configurable N, recommended buckets
  after deliverability, internal monitoring before customer reminders. The report labels its
  present age basis rather than claiming the recommended deliverability rule is implemented.
- Seat 31 Admin release notices, 10–11 November: invoice reads/mailboxes are live. Existing supplier
  bill filtering and optional blocked coverage are preserved; this change doesn't claim a new live run.
- Deepak's ownership split: section 1 and report runner here; invoice/payment/draft modules stay his.
- PR #13/#17 reviews: preserve UTF-8, shared Markdown cell escaping, currency-source metadata,
  supplier exclusions and proposal-only defaults. No cross-currency financial aggregate is added.
- Open #21 replaces closed-unmerged #12. Its graders observe state, not printed answers; passing
  our report tests is not certification of a Seat 34 graded run. Its command/token/cwd requirements
  belong to the later entry-point work.
- #24/#25's deterministic Seat 35 adapter and instructor-scored pairs-file exception are seat-specific;
  no equivalent Seat 31 grading exception is assumed here.
- Merged #15/#16/#23 review guidance: keep scope within the seat, keep approval steps explicit,
  avoid write calls and explain the run command clearly. Open #26's default-draft behaviour is not copied.
- Closed/merged Red Team work and open #19/#20/#27/#28 motivate preserving read-only execution and
  not publishing sensitive platform material; they don't define new section-1 financial rules.
- Open #11's client guards and #21's timeout/logging reviews remain inputs for later client/runner work,
  not a reason to expand this report PR into unrelated authentication or process-supervision changes.

The inventory of all 28 PRs was checked for status; selected relevant code/docs/reviews were examined.
This is not an assertion that every historical PR received a full code review.

## Temporary base and integration

Implemented on `seat31/sunil-report-tests`, based on PR #17 head
`0b1540b93ca3a1d3f3182949e8d1628e536557fd` while #13 and #17 are still open. It includes #13's latest
receivable-only behaviour. This does not represent either PR as merged or approved at that head.
Only this branch's report/test/doc changes should be replayed onto the eventual merged main.
Do not copy the whole inherited tree into another report implementation.

The known #17 received-mail attribution counterexample is inherited and unchanged. Its tests may
pass despite that verifier limitation. Resolve it with Deepak independently; no strengthened
approval or full-agent safety claim is implied by this report branch.

## Tests and output evidence

`tests/test_order_report.py` adds ordinary synthetic unit and CLI tests, not graded course tasks.
They cover thresholds, buckets, dates/provenance, oldest-first/currency/value ties, stable IDs,
resolved currency, missing values, legacy render compatibility, diagnostics, CLI validation,
Unicode/table escaping, blocked invoice coverage and allowed read calls.

Retain the inherited report/draft/matching/refusal regression suite. Run the entire discovery command
in the README and compare synthetic report/proposal outputs with explicit expected IDs/amounts.
No live records or model calls are needed. Final observed totals and private logs are recorded in
the tracker implementation summary rather than assumed here.

Human authors must still write and label graded tasks. The known partially-paid presentation,
overdue-invoice digest section, receipt-source policy and external-mail attribution questions are
not marked complete by this work.

## Review follow-up: pagination (12 November)

Addresses PR #29's missing-total pagination thread. The chosen policy is explicit protocol failure,
not a fallback assumption that an omitted total means zero. This deliberately expands the original
report-only follow-up to the shared list client, as requested in review; other client hardening is
still separate. No authentication or draft/refusal policy is changed.

`list_all` returns only after the distinct returned records reach the validated, stable integer total.
Incomplete/malformed pages, repeated pages/IDs and page-budget exhaustion raise `PaginationError`.
Exceptions contain protocol facts rather than row contents or credentials. No new report files are
published after a source fails, including when an earlier section was read successfully.

`tests/test_pagination.py` covers the protocol, the 501st record, page offsets/filters, bounded fetches,
transport failure, and complete/incomplete CLI publication. The missing-total regression was checked
against the old code before implementation and reproduced the silent-return bug.

This does not fix the other open review threads (strict shared dates/amounts, instance path validation,
publication failures after output writing starts, or prerequisite/rebase scope).
