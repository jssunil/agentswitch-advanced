# Plan: `seat31/sunil-v1` (Sunil's half of version 1)

Written 20 Nov 2026. One PR, one topic per commit. PR by **25 Nov**; seat 34's recount is on **30 Nov**.
Covers items 1 and 2 of the 18 Nov phase 2 plan, plus suite tasks 1–4 and 11, the agent hook and the
`results.json` runner. Deepak's half (P2 closure, bug-hunt fixes, task 12, README) is in #54.

## Starting point

- #54 (`seat31/deepak-v1`) merges first, planned for 22 Nov.
- Tasks 1–4 are commit 1 on this branch. Task 12 is left out, because #54's `report_read_only` replaces it.
  (They were first written on `seat31/sunil-order-tasks`, now deleted.)
- A trial merge of these tasks onto #54 had no conflict in the task files, and 358 offline tests pass.

**Step 0 (partially done; rebase pending #54 merge):** after #54 merges, `git fetch upstream && git rebase upstream/main` (or `git rebase --onto upstream/main 7797482` if #54 is squash-merged). Switching `--days` in `o2c/tasks_orders.py` to #54's `output.days_option` is already applied.

## Commits

| # | Commit | Files | Notes |
|---|---|---|---|
| 1 | Suite tasks 1–4 (orders not invoiced) — **done** | `o2c/tasks_orders.py`, `tests/test_tasks_orders.py` | Task 12 never appears; `python3` in run lines; `--days` uses `output.days_option` |
| 2 | Item 1: print the order IDs, one per line — **done** | `o2c/__main__.py`, `o2c/output.py`, `tests/test_order_ids.py` | Order record `id`s on stdout, oldest first; summary on stderr; exit **4** when incomplete |
| 3 | Item 2: use `--prompt` for "which orders" — **done** | `o2c/prompt.py`, `o2c/__main__.py`, `tests/test_prompt_compatibility.py` | Reads days or order IDs; `--days` wins; unreadable prompt gives default report; never writes/sends |
| 4 | `AGENT_RESULT` line — **done** | `o2c/__main__.py`, `o2c/output.py`, tests | `{"claim":"refused"}` for write/send prompts, `"done"` otherwise, with `answer.orders_not_invoiced` |
| 5 | Suite task 11, `refuse_edit_foreign_order` — **done** | `o2c/tasks_orders.py`, `tests/test_tasks_orders.py` | Includes review fixes (exit-code failure check, other-login attribution, `o.get("id")` safety) |
| 6 | Agent hook for tasks 10 and 11 — **done** | `o2c/agent.py`, `tests/test_agent_hook.py` | `CommandAgent` passes proxy/cert/system env vars and fails with `outcome=infrastructure` on crash/timeout |
| 7 | Runner that writes `results.json` — **done** | `o2c/suite.py`, `tests/test_suite_runner.py` | Format from `34-eval-harness/TASK_FORMAT.md`. Task ids come dynamically from `tasks_orders.TASKS` and `tasks_payments.TASKS`; crashes surface as `outcome=infrastructure` |
| 8 | README and PLAN — **done** | `README.md`, `PLAN.md`, `docs/sunil-v1-plan.md` | Filled the three placeholders (ID list for seat 34, `--prompt`, suite tasks and runner), documented exit `4`, added section 1 demo commands, and marked the 12-task suite done |

## Outside the commits

- Record the section 1 demo parts (ID list + seat 34 recount; a prompt-driven run) by 29 Nov.
- Joint test with seat 34 on both systems before 30 Nov.

## Waiting on seat 34 (asked 19 Nov in the ADV channel)

The answers may change commits 2–4:

1. Which orders qualify, and which cutoff date both sides use (commit 2).
2. The exact first task prompt (commit 3's parser and tests).
3. How the harness scores exit 0 with an empty list, exit 3 and exit 4 (commits 2 and 4).
4. A joint test with an answer longer than the 4,000-character output tail (commit 4).

Commits 1, 5, 6 and 7 don't depend on these answers and can start now.
