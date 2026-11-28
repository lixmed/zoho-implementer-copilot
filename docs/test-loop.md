# Deluge test loop (Phase 5)

Runs a function's test cases in a **developer, sandbox or test** org, feeds failures back to the agent, and re-tests after a human-confirmed re-push.

## One-time setup per org
```powershell
uv run copilot connect --name "Company Sandbox" --slug company-sandbox --env sandbox --allow-writes --allow-test-records --test-modules Leads,Deals
```
- `--allow-test-records` adds **record access only for the listed modules**, plus the function execute scope.
- The execute scope (`ZohoCRM.functions.execute.READ/CREATE`) is **not in Zoho's current docs**. If the consent page rejects it, reconnect with `--no-execute-scope` and use an API-key URL (below).

Per function, one manual step: **Setup → Developer Hub → Functions → \<function\> → REST API** (turn on OAuth, or API key).

## Core + wrapper (automation functions)
The tool never attaches functions to workflows, so automation logic is written to be testable on its own:
- `post_hot_lead_core(String record_id)`: **standalone**, holds all the logic, returns the result map.
- `post_hot_lead`: the **automation** function, a thin wrapper that calls the core and logs the result.

Both are saved with `save_deluge` and pushed with `copilot deluge push`. The core is accepted automatically as a companion of the planned automation function.

## Test cases (`<name>.tests.json`)
```json
[
  {"name": "counts hot leads", "input": {}, "expected": {"status": "ok"}},
  {"name": "sets referral", "module": "Leads",
   "record": {"Last_Name": "COPILOT TEST", "Lead_Temperature": "Hot"},
   "input": {"record_id": "$record_id"},
   "expected": {"status": "ok"},
   "expect_record": {"Lead_Source": "Referral"}}
]
```
- `expected` is a subset match on the returned result map.
- For record tests, a fake record is created with **automation suppressed** (`"trigger": []`). Its id replaces `$record_id`, it's read back to check `expect_record` (only those fields), and it's **always deleted** afterwards. If cleanup fails, the test is marked failed with the record id, so it can be removed by hand.

## Commands
```powershell
uv run copilot deluge test  <project> <function>     # one run
uv run copilot deluge loop  <project> <function>     # run -> agent fixes -> you confirm push -> re-run
uv run copilot deluge set-url <project> <function>   # API-key URL fallback (hidden input, keychain)
uv run copilot deluge metrics <project>              # runs per function, first-placement pass rate
```
The **loop** is started by you. When a run fails it waits (default 10 minutes) for the agent to save a fix (`get_test_results` → fix → `save_deluge`). It notices a fix by **comparing code content, not file timestamps**. Then it shows you the diff, pushes the update only after you type the function name, and runs again (default up to 5 runs).

## Results and metrics
- `handoff/functions/<name>.testrun.json`: the latest run (the agent reads it via `get_test_results`)
- `handoff/functions/<name>.history.json`: every attempt, with a code hash
- `copilot deluge metrics`: `passed_first_placement` per function and the overall **first-placement pass rate**, used by the eval suite in Phase 6

## Safety
- Test orgs only (developer, sandbox, test). Production is refused.
- Every record create and delete and every execute goes to the audit log (argument keys only, not values).
- Function output shown to the agent is truncated and has contact details masked. Only the compared fields of test records are returned.
- An API-key URL is accepted only if it's **this function's** execute URL on **this org's** Zoho API host.

## Unverified until the company sandbox
- The execute scope name.
- Passing arguments as the `arguments` form field (the classic documented format; not shown on the v8 pages).
- The execute response shape (`details.output`).
- Deluge syntax for the wrapper calling its core function.
