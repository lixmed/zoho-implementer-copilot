# Testing, all free

## Level A: no Zoho account at all (runs in CI)
```powershell
uv run pytest
```
- **Unit tests:** OAuth parsing, the DC allowlist, the token manager, the read client (401 refresh, 429, 204, caching), the registry and the keychain.
- **End-to-end against a fake Zoho** (`tests/fake_zoho.py` + `tests/test_e2e.py`): `copilot connect` starts the real localhost callback server, a simulated browser redirect hits it, tokens land in the (in-memory) keychain, `copilot snapshot` writes JSON, and the real MCP server answers tool calls.
  It also covers the attacks: tampered `state`, a foreign `accounts-server`, and a production token when you declared developer.
- **The full story** (`tests/test_story.py`) is one test that walks a whole engagement:
  1. Connect the dev org (real PKCE and callback server), snapshot it, check users are masked.
  2. Create a project. The client sends a .docx (with a table) and an email.
  3. The agent drafts a plan over MCP. `check_plan` catches a field that doesn't exist, the agent fixes it, and saves v1.
  4. A wrong-slug approval is refused, then the human approves at the CLI.
  5. The sign-off doc and UAT script are exported.
  6. Deluge: the agent gets the function brief. Bad code (hard-coded token, no connection) is refused, and good code is saved with tests. A wrong typed name means nothing is sent. Zoho's compiler rejects the first push, the error is read back, and the second push succeeds. Only the functions endpoint is written to (no workflow calls), and the audit log has both attempts.
  6b. Test loop: the agent saves `post_hot_lead_core` with a record test. You push it, `deluge test` creates a fake Lead, executes the core, compares, and deletes the record. `get_test_results` shows 1 passed, and the metrics show it passed on first placement.
  7. Builder: `preview_changes` lists the operations (a field, a picklist add) and the handoff items. A wrong typed slug means nothing is sent. The correct slug applies (audited), then a soft rollback runs with no DELETE calls.
  8. The client emails a change request (new feature, one withdrawn). Old unit ids don't move. The agent saves v2.
  9. `plan diff` produces the change request against the approved version, with the effort delta.
  10. The project ends: `orgs remove --revoke`, and the keychain and registry are empty.

  Run just the story with `uv run pytest tests/test_story.py -v`.
- The fake responses follow the v8 docs and are **not recorded from a real org yet**. Level B fixes that.

## Level B: a free real Zoho org
Pick one (both cost nothing):

| Option | Cost | What you get | Limits |
|---|---|---|---|
| **Zoho CRM Developer Edition** (recommended) | Free | A full-featured org for building and testing (Zoho developer page) | Per-feature caps: about 10 workflow rules per module, 100 function calls per day. The edition page dates from 2018, so check it when you sign up. |
| Zoho CRM Free edition | Free, up to 3 users | Basic CRM | 5,000 API credits/day, concurrency 5, many automation features missing, **no sandbox** |

Sign up yourself at https://www.zoho.com/crm/developer/developer-edition.html with an email you control. Use only fake data in it (project rule 7).

Then:
1. Register a **Server-based** client at https://api-console.zoho.com with redirect `http://127.0.0.1:8765/callback`.
   - If Zoho rejects that URI, try `http://localhost:8765/callback` and tell me which one worked (it's an open Phase 0 question).
2. Run `uv run copilot client set --dc us` (or the DC your org lives in).
3. Run `uv run copilot connect --name "My Dev Org" --env developer`.
   - If Zoho issues a token for a different environment than `developer`, the tool refuses and revokes it. That itself is a useful finding: rerun with the `--env` it reported.
4. Run `uv run copilot snapshot my-dev-org`, then open `snapshots/my-dev-org.json` (gitignored).
5. Hook it up to Claude Code (see setup.md) and ask: "What custom fields does Leads have?"

**API cost of a full test run:** a snapshot of a fresh dev org is about 30 GET calls at 1 credit each. That's nothing against 5,000 or more credits a day.

## Level C: Zoho sandbox (later, Phase 4)
Sandboxes come with Enterprise and Ultimate (paid), or possibly with the developer edition (unverified). Until we know, the developer org itself is our "sandbox" target for writes.

- **Builder** (`tests/test_builder.py`, stateful fake in `tests/fake_builder.py`): preview order, apply, soft and hard rollback, stop on failure plus rollback, idempotent re-run, typed confirm, gates (scopes, production, edited plan, missing section).
