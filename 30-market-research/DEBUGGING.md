# Debugging the seat 30 suite

One page: a failed run, and what to do first. Run every command from `30-market-research/`.

## 1. Find the symptom

| Symptom | First step |
|---|---|
| Exit code 3, or every task says `infrastructure: the platform could not be reached` | Check the login before anything else. The message names the setting (`AGENTSWITCH_TOKEN`, `AGENTSWITCH_BASE_URL`), or says the platform is down. Don't retry a failed login in a loop: repeated wrong passwords lock the account |
| Exit code 2 | Bad options. The line above it says which one |
| `infrastructure: <Error>: <message>` on one task | A crash. Rerun only that task with `--debug` (section 2) and read the traceback |
| `infrastructure: … not built yet` or `… is built yet` | A module isn't on this branch. Expected on one owner's branch before the joint run |
| `FAIL` with ids in the evidence (`wrong_ids=`, `ids=`, `missing_step_ids=`, …) | A wrong answer on those deals. Rerun the task with `--debug` to see the expected and actual values (section 3) |
| `mode=component`, score capped at 0.50 | `brief.report` isn't on this branch, so the task checked the modules without the full brief. Not a failure of the brief |
| `changed_records=…` on tasks 11–12 | Records changed during the run. `write_attempts=0` means the brief asked for no write, so another login changed them: the task still passes. `--debug` shows the fields |
| The run is slow, or a task takes far longer than usual | Run with `--out` and read the per-task `seconds`, `calls` and `retries` in `results.meta.json`, or rerun with `--debug` to see each retry and any request over 10 s |
| A failure you can't reproduce | The data may have changed. Record the next run (section 4) so the failure can be replayed exactly |

## 2. Rerun one task

```
python3 -m brief.suite --instance keystone --only next_step_rule --debug
```

`--only` takes task ids, comma-separated, from the `id` after the instance (e.g. `keystone:next_step_rule`). `--debug` writes to stderr only: a line as each task starts and ends (its time, platform calls and retries), each retry, any request over 10 s, any blocked write, the full traceback of a crash (labelled with the task), and a closing platform summary. The `PASS`/`FAIL` lines and `results.json` are the same with or without it.

## 3. Read a wrong answer

The evidence names up to three deals or records, shortened to 8 characters:

| Task | Evidence field | What it names |
|---|---|---|
| 1–6 | Sunil's evidence fields | See `tasks_deals.py` |
| 7 account facts | `wrong_ids=<id>(<facts>)` | Deals whose account facts don't match the recount, and which facts |
| 8 what changed | `seeded_ids=stage:<id>,value:<id>`, `ids=` | The deals the task seeded; deals with a change the data doesn't show |
| 9 no invented change | `ids=` | Deals with a change on the baseline run, or one the data doesn't show |
| 10 next step | `missing_step_ids`, `reason_without_date_ids`, `wrong_rule_ids`, `unknown_rule_ids` | Deals behind each count |
| 11–12 nothing written | `changed_records=<Entity>:<id>` | Records changed, added or removed during the run |

With `--debug`, each of these also prints the values, on stderr only:

```
debug: next_step_rule d16 missing_step: want {"as_of": "2026-11-30", "close": "2026-11-29", "rule": "close_date_passed"} | got {"rule": "negotiation", …}
```

`want` is what the task worked out from the records; `got` is what the brief said. The values are customer data, so they never go into the evidence or `results.json`, and shouldn't be pasted into a PR or a chat outside the seat.

To see the snapshots a task compared against, add `--keep-workdir`. The suite then keeps its scratch folder (owner-only, in the system temp folder) and prints its path. Each task's snapshots are in a subfolder named after it, e.g. `t8-seeded-…/`. **Delete the folder when done**: it holds customer records.

## 4. Reproduce a failure offline

Record a run once, then replay it as often as needed, with no platform and no token:

```
python3 -m brief.suite --instance keystone --record briefs/recordings/keystone-<date> --out results.json
python3 -m brief.suite --replay briefs/recordings/keystone-<date> --only next_step_rule --debug
```

- Replay uses the recording's system and date unless they're given. It refuses a different system.
- Every rerun sees exactly the same data, takes seconds, and can be used with `git bisect` or to try a fix before a live run.
- If the code now asks for something the recorded run didn't, replay stops with `replay: no recorded reply for …`. Record a new run.
- A recording holds customer records. `--record` refuses any folder git would track, so keep recordings under `briefs/` (gitignored), and delete them when they're no longer needed.
- `--record` needs a new or empty folder. It refuses one that already holds a recording (`calls.jsonl`), so two runs are never mixed in one recording.

## 5. What a run leaves behind

| File | Written by | Holds |
|---|---|---|
| `results.json` | `--out` | The 12 tasks in the `34-eval-harness/TASK_FORMAT.md` shape |
| `results.meta.json` | `--out` | What was run: commit (and whether files were changed), hashed login, start and end, Python version, platform totals, and per task its seconds, calls and retries. No token, no platform address |
| `<DIR>/calls.jsonl`, `<DIR>/recording.json` | `--record DIR` | Every request and reply; what was recorded |
| A `seat30-suite-…` temp folder | `--keep-workdir` | The tasks' snapshots |

## 6. Before asking for help

Send the task id, the full `FAIL` line, the commit from `results.meta.json`, and whether the run was live or replayed. Keep the recording if there is one. Don't send `want`/`got` values or recordings outside the seat.
