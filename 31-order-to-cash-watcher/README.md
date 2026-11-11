# Seat 31: Order-to-Cash Watcher

## Scope
Follows sales orders to invoices to payments. Finds stuck orders and overdue invoices and drafts reminders for a person to send.

## First task
A daily exception report: orders not invoiced after N days, invoices overdue, payments not applied.

## Access
crm, accounting, inventory (read), email

## Milestones
- 15 Nov 2026: first task delivered
- 30 Nov 2026: version 1, with a 12-task test suite
- 20 Dec 2026: integration run of all eight seats
- 31 Dec 2026: final

## Working rules
See the top-level README. Put your agent code, tests and notes in this folder; open a pull request for every change.

## Daily exception report

```bash
cd 31-order-to-cash-watcher
python -m o2c --instance keystone --date 2026-11-15 --days 7 --out reports/
python -m unittest discover -s tests -t . -v
```

The report reads `AGENTSWITCH_BASE_URL` and `AGENTSWITCH_TOKEN`. It never logs in or creates a
platform record. Without `--out` it prints Markdown; with it, it writes UTF-8 JSON and Markdown.
Use an explicit date for reproducible checks. `--days` / `O2C_DAYS` must be a nonnegative integer.

Orders qualify when submitted, unbilled or partially invoiced, and strictly more than N days old
by **order date**. No verified deliverable-date source is agreed yet. All qualifying orders are
therefore marked `deliverability_status=unverified`, not assumed ready for billing. The existing
`delivered_status` remains visible but does not establish a deliverable date.

- Oldest first. Equal ages are grouped by currency (known codes A–Z, unknown last), then by
  descending value within each currency, then number and ID. Values in different currencies
  are not compared or converted. Sorting uses resolved record/customer/company currency.
- Additive row fields: `age_basis`, `age_bucket`, `deliverability_status`.
- The order section adds `age_basis`, `deliverability_status`, `unverified_deliverability_count`,
  `age_buckets`, and `date_issues`. The unverified count describes the same canonical rows;
  it is not a second financial total.
- Buckets are 0–7, 8–14, 15–30, 31–60 and >60 **order-age** days. They count exception rows only.
  At N=7 the first bucket is empty by design: the strict threshold still excludes age 7.
- Submitted unbilled orders with missing/invalid/future dates appear in separate date diagnostics
  and are excluded from age-based rows and bucket counts. They must be investigated, not treated
  as confirmed non-exceptions. Other report sections retain their existing date handling.

The original section headings and table columns remain; consumers should accept additive JSON
metadata and explanatory Markdown paragraphs. Invoice access unavailable means **blocked**, not zero
overdue. Supplier bills remain excluded from customer receivables. Payment/matching/draft code is
unchanged by this report follow-up. Default execution remains file-only and read-only; approved
draft creation uses the separate drafts command and explicit confirmation.

See [report implementation notes](docs/report-followup.md) for scope, assumptions and validation.
