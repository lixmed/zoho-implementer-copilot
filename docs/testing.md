# Testing, all free

## Level A: no Zoho account at all (runs in CI)
```powershell
uv run pytest
```
- **Unit tests:** OAuth parsing, the DC allowlist, the token manager, the read client (401 refresh, 429, 204, caching), the registry and the keychain.
- **End-to-end against a fake Zoho** (`tests/fake_zoho.py` + `tests/test_e2e.py`): `copilot connect` starts the real localhost callback server, a simulated browser redirect hits it, tokens land in the (in-memory) keychain, `copilot snapshot` writes JSON, and the real MCP server answers tool calls.
  It also covers the attacks: tampered `state`, a foreign `accounts-server`, and a production token when you declared developer.
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
