# Plan: `seat31/sunil-v1` (Sunil's half of version 1)

Written 20 Nov 2026. One PR, one topic per commit. PR by **25 Nov**; seat 34's recount is on **30 Nov**.
Covers items 1 and 2 of the 18 Nov phase 2 plan, plus suite tasks 1–4 and 11, the agent hook and the
`results.json` runner. Deepak's half (P2 closure, bug-hunt fixes, task 12, README) is in #54.

## Starting point

- #54 (`seat31/deepak-v1`) merges first, planned for 22 Nov.
- Tasks 1–4 are already written on `seat31/sunil-order-tasks`. Task 12 was dropped there (`9bc11b7`), because #54's `report_read_only` replaces it.
- A trial merge of `seat31/sunil-order-tasks` onto #54 has one conflict, the usage lines in `o2c/drafts.py`'s docstring (keep #54's), and 358 offline tests pass.

**Step 0 (not a commit):** after #54 merges, `git fetch upstream && git rebase upstream/main`, then bring over
`seat31/sunil-order-tasks`.

## Commits

| # | Commit | Files | Notes |
|---|---|---|---|
| 1 | Suite tasks 1–4 (orders not invoiced) | `o2c/tasks_orders.py`, `tests/test_tasks_orders.py` | Squash `4334bd8` + `9bc11b7`, so task 12 never appears; fold in `fbdd435` (`python3` in run lines), keeping #54's docstring where they conflict. `--days` uses `output.days_option` |
| 2 | Item 1: print the order IDs, one per line | `o2c/__main__.py`, `o2c/output.py`, new test | Order record `id`s on stdout, oldest first; the summary moves to stderr. Exit **4** when the list may be incomplete (unusable dates, partial coverage); 2 and 3 keep their meaning. Test: printed IDs equal the report JSON rows exactly |
| 3 | Item 2: use `--prompt` for "which orders" | new `o2c/prompt.py`, `o2c/__main__.py`, `tests/test_prompt_compatibility.py` | Reads a number of days or specific order IDs. `--days` wins over the prompt. An unreadable prompt gives the default report. A prompt never causes a write, send or draft. Tests cover readable, unreadable and instruction-like prompts ("mark these as billed", "send reminders now") |
| 4 | `AGENT_RESULT` line | `o2c/__main__.py`, tests | `{"claim":"refused"}` for a write or send prompt (refusal tasks A1–A6); `{"claim":"done","answer":{"orders_not_invoiced":[…]}}` for the recount prompt. Always the last line: the harness keeps only the last 4,000 characters of output |
| 5 | Suite task 11, `refuse_edit_foreign_order` | `o2c/tasks_orders.py`, tests | Guardrail-only (score 0.5) until commit 6, like task 10 |
| 6 | Agent hook for tasks 10 and 11 | task context, runner | `Ctx(agent=callable)`: the callable runs `python -m o2c --prompt "…"` and returns its output, so both tasks test the real agent |
| 7 | Runner that writes `results.json` | new runner module, tests | Format from `34-eval-harness/TASK_FORMAT.md`. Task ids come from both `TASKS` lists (7 in `tasks_payments`, 5 in `tasks_orders`), never a fixed count. A task that crashes comes back as `outcome=infrastructure` |
| 8 | README and PLAN | `README.md`, `PLAN.md` | Fill the three placeholders (ID list for seat 34, `--prompt`, suite tasks and runner); add the section 1 demo part; mark the 12-task suite done |

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
