# Security model

_Living document, updated each phase. Current: Phase 1 (read-only)._

## Assets
Client refresh tokens, the Zoho client secret, client org metadata, and (never in scope) customer records.

## Controls in place
| Threat | Control |
|---|---|
| Token theft from disk | Refresh tokens and client secrets live only in the OS keychain. `orgs.json` holds no secrets. Access tokens stay in memory. |
| Secrets in logs or repr | Credential objects override `__repr__`. The callback server suppresses request logging, so the auth code never reaches the console. |
| Auth code / secret sent to a fake server | The `accounts-server` from the callback must be on the Zoho DC allowlist (https, exact host, no path or userinfo). Every token request checks it again. |
| CSRF on the OAuth callback | A random `state` compared in constant time, plus PKCE S256. |
| Other machines hitting the callback | It binds to `127.0.0.1` only, serves a single request, and times out after 3 minutes. |
| Customer PII reaching the LLM | Level 1 requests **no `ZohoCRM.modules.*` scopes**, so records are unreadable. Users are masked with an allowlist. Metadata views are allowlisted too. |
| Accidental writes | `ZohoReadClient` has no write methods. Every MCP tool is annotated `read_only_hint=True`, and a test asserts that no write tools exist. |
| Wrong environment | The environment is detected from `api_domain`. If it doesn't match what the user declared, the token is revoked and nothing is saved. Production connections are blocked until Phase 7. |
| Client wants out | `copilot orgs remove <slug> --revoke` revokes the token at Zoho. The client admin can also revoke it from their Zoho account at any time. |

## Planned
- Phase 4: write tier with approval gates, a SQLite audit log (who, what, when, org, before/after), and snapshots and rollback.
- Phase 7: production profile with a typed confirmation, and API credit budgets.
