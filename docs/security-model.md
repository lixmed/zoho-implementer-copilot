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

## Phase 2 additions
| Threat | Control |
|---|---|
| Client documents leaking via git | Project workspaces live in a user-chosen folder outside the repo. `snapshots/` and `handoff/` are gitignored. |
| Contact details in requirements reaching the LLM | Emails and phone numbers are masked when units are extracted. Email signatures and quoted replies are dropped. |
| Agent approving its own plan | There is no approve tool over MCP. Approval is a CLI action with a typed confirmation, logged with approver, time and SHA-256. |
| Approved plan edited afterwards | The hash no longer matches, so the plan counts as not approved. |
| Planner writing to Zoho | The planner tier writes only local files (`open_world_hint=False`). Zoho access stays read-only. |

## Phase 3 additions (first writes to Zoho)
| Threat | Control |
|---|---|
| Agent writing to Zoho on its own | No push or attach tool over MCP. `copilot deluge push` is a CLI action with a typed function name. |
| Writing unapproved code | Push requires an approved plan, unchanged since approval (SHA-256), containing that function. |
| Writing to production | The write client refuses production orgs. Push only targets developer or sandbox. |
| Broad write permissions | Write scopes are opt-in (`--allow-writes`) and limited to `ZohoCRM.settings.functions.CREATE/UPDATE`. |
| Dangerous code shipped | Static checks block hard-coded secrets, manual auth headers, undeclared connections and unknown modules or fields. |
| Code running on records unexpectedly | Functions are saved but never attached to workflows. Attaching is a separate approved handoff step. |
| No trace of changes | Append-only SQLite audit log (UPDATE and DELETE blocked by triggers): actor, time, org, environment, plan version, before/after, result. |
| Doc lookups leaking project details | Search ranks locally, so queries never leave the machine. Fetches are allowlisted to zoho.com doc paths, redirects included. |

## Phase 4 additions (Builder)
| Threat | Control |
|---|---|
| Applying unreviewed changes | Preview first (operations and credits). Typed project slug. Approved, unchanged plan only. No apply or rollback tool over MCP. |
| Data loss on rollback | Soft rollback by default (hide, deactivate, restore from snapshot). Hard delete needs a typed `DELETE` that names the data loss. |
| Half-applied change sets | Stop at the first failure, save state, offer rollback. Every operation's status is recorded. |
| Drift between plan and org | The change set is built against live metadata. Existing items are skipped, and missing layouts or sections block the apply. |
| Untraceable changes | `before.json`, `after.json` and `diff.json` per change set, plus the append-only audit log. |
| Guessing API formats | Only operations with verified request shapes are automated. The rest stay handoff steps. |

## Phase 5 additions (test loop)
| Threat | Control |
|---|---|
| Record access creeping in | Record scopes only with `--allow-test-records`, per listed module, on developer/sandbox/test orgs only. |
| Test data leaking or lingering | Fake values from tests.json. Automation suppressed on insert. Records always deleted (failed cleanup is reported with the id). Only the compared fields are read back. |
| Customer data reaching the LLM via outputs | Output truncated to 2 KB with contact details masked. Test orgs only. |
| Re-pushing code without consent | The loop shows a diff, and the human types the function name before every push. |
| A pasted API-key URL sending data elsewhere | Accepted only if it's this function's execute URL on this org's own Zoho API host. Stored in the keychain. |
| Untraceable executions | Every create, delete and execute is audited (argument keys only). |

## Planned
- Phase 4: generalize the write tier (fields, layouts, workflows) with snapshots, diff and rollback, reusing the Phase 3 audit log and approval gates.
- Phase 7: production profile with a typed confirmation, and API credit budgets.
