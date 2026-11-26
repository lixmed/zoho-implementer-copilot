# Interview Notes

## Phase 0: API coverage check

**Q: Why did you check API coverage before writing any code?**
Because the architecture depends on it. If functions couldn't be deployed via API, the "debug loop" would be copy-paste only. If connections could be created via API, the security model would change. Checking first told me the real split: fields, layouts, workflows and function code are automatable. Connections, validation rules, blueprints and buttons are UI-only. So the "handoff pack" is a core feature, not a fallback.

**Q: Zoho already has an MCP server that can create fields and workflows. Why build this?**
Zoho's hosted MCP servers give an AI raw write access to one org. A partner needs a delivery process on top of that: requirements → a reviewable plan → approval → sandbox → diff → rollback → audit, across many client orgs, with customer data kept away from the LLM. Zoho's own servers have no plan, diff, rollback or audit step, and their record tools expose PII. I call the REST API directly so that every write passes through my own approval and audit layer.

**Q: How do you keep customer PII away from the LLM?**
First at the OAuth level: the planner requests only `ZohoCRM.settings.*.READ` and `ZohoCRM.users.READ`, never `ZohoCRM.modules.*`, so it *cannot* read records. Second, user names and emails from `/users` are masked before they reach the model. Record scopes are requested only on the sandbox org, for behavior tests that use fake data.

**Q: What stops the agent from deleting a field in production?**
Delete endpoints are in a separate "danger" tier. Sandbox and production are separate org profiles with separate tokens, because Zoho tokens are environment-specific. Production writes need a production flag and a typed confirmation. Before any rollback that deletes a field, the tool checks the record count, because deleting a field destroys its data.

**Q: What does your tool do that Zoho's own MCP can't?**
Zoho's MCP executes changes. My tool delivers projects. The headline feature is traceability: every plan item links back to the exact sentence in the client's requirements, so there's a coverage matrix showing nothing was missed and nothing was invented. When the client changes their mind, the tool diffs the new plan against the approved version and estimates each change, so scope creep is visible and billable. It also generates the client sign-off doc and the UAT script from the same plan. And because the eval suite scores "requirements not addressed" and "items with no source", traceability doubles as a hallucination check.

**Q: How does the tool handle Zoho's data centres?**
It never hardcodes the API URL. Authorization starts at accounts.zoho.com. The callback says which accounts server the user belongs to, and the token response returns `api_domain`. Both are stored per org profile.

## Phase 3: Deluge generation

**Q: How do you make generated Deluge use real functions and real field names?**
Two sources of truth, both outside the model. First, a function brief built from the org's live metadata plus the fields the plan creates. The model gets the exact api_names and nothing else. Second, live lookups in Zoho's official docs for every task it uses, scoped to the app, because Deluge differs between CRM, Books and Creator. Then static checks verify the code against those same names before anything leaves the laptop.

**Q: Why search docs live instead of building a vector database?**
The user's call, and it fits: Deluge docs change and differ per app, and a stale index produces confidently wrong code. I didn't want a search API key or to send queries out, so the tool downloads Zoho's public per-app task index and ranks it locally. The query never leaves the machine, only public doc pages are fetched, and fetches are restricted to an allowlist of zoho.com paths (redirects included).

**Q: What do your static checks catch?**
Wrong declaration or return type for the category, unbalanced brackets, hard-coded tokens and keys, `invokeurl` without a connection or with an undeclared one, modules and fields that don't exist, hard-coded record ids (they break when you move from sandbox to production), missing null checks, and missing logging. Errors block saving and pushing. Zoho's compiler is still the final judge, and its line and position come back to the agent.

**Q: The tool writes to Zoho now. What stops it doing damage?**
Six gates: there's no push tool in MCP, so a human runs the CLI. The plan must be approved and unchanged since approval (hash check). The org must be developer or sandbox. Write scopes are opt-in and limited to creating and updating functions. Static checks must be clean. And the human types the function name. The write client is a separate class that refuses production, and every call lands in an append-only SQLite audit log with before and after. The function is saved but never attached to a workflow, so it can't run on records until someone approves that separately.

**Q: Why not store the code in the audit log?**
The code is already versioned in the project's handoff folder. The audit log records what happened (who, when, which org, plan version, code length, Zoho's response). Keeping it small and free of payloads makes it easy to share with a client's admin.

## Phase 2: Planner with traceability

**Q: How do you stop the LLM from hallucinating scope or quietly dropping requirements?**
I don't let it grade itself. Every requirement sentence gets an id, and every plan item must cite the ids it comes from. The *server* computes coverage from those citations. If any sentence is neither cited, asked about, nor explicitly marked out of scope with a reason, the plan can't be saved. Items that cite nothing fail schema validation, so invented scope has nowhere to hide.

**Q: How do you stop it inventing field names?**
Validation checks every `Module.Field` reference against the org's live metadata plus the fields the plan itself creates. A workflow on a field that doesn't exist is an error, and the agent sees it and has to fix it before saving.

**Q: Who decides what can be automated?**
Not the LLM. Each item's delivery mode (API / handoff / decision) is derived from the Phase 0 API research table in code. If Zoho has no API for validation rules, the plan can't claim one will be created automatically.

**Q: Why can't the agent approve its own plan?**
There's no approve tool in the MCP server. Approval is a CLI command where a human types the project slug. It records who, when and a SHA-256 of the plan file, so editing an approved plan silently voids the approval. That's a classic separation of duties.

**Q: How do change requests work?**
Plans are versioned, and the agent keeps item ids stable when revising. A diff against the last *approved* version shows exactly what was added, removed and changed, and the effort delta, which becomes a client-facing change request. Scope creep becomes visible and billable.

**Q: Why let Claude Code do the reasoning instead of calling the API from your server?**
Cost and time to value: it works today with no API key. The important part, the rules, lives in the server, so it doesn't matter which agent drafts. For repeatable evals I'll add my own API loop later; it will hit the same validation.

## Phase 1: OAuth, keychain, read-only MCP tools

**Q: Why MCP?**
MCP is a standard way to expose tools to any AI client. I write the Zoho access layer once, and Claude Code, Claude Desktop, or my own agent loop later can all use it. More importantly, the safety rules live in the server, not in a prompt. A prompt can be ignored; a server that has no write method cannot write.

**Q: How do you keep client tokens safe?**
Refresh tokens never expire in Zoho until revoked, so they're effectively passwords. They go only into the OS keychain (Windows Credential Manager / macOS Keychain), never into a file, an env var or a log. Access tokens live in memory for an hour. The org registry on disk has names and data centres only. Credential objects even override `__repr__`, so a stray print or traceback doesn't leak them.

**Q: What stops someone stealing the OAuth code?**
Three things. PKCE: the code is useless without the verifier, which never leaves the process. `state`: a random value compared in constant time, which blocks CSRF. And an allowlist: the callback tells us which Zoho accounts server to use, and I check it's a real Zoho data centre before sending the client secret there. Otherwise a tampered redirect could send my secret to an attacker's host.

**Q: How is "read-only" enforced, not just promised?**
Structurally. The client class only has a `get` method. Every MCP tool is annotated read-only, and a unit test fails if anyone adds a tool named create/update/delete. The OAuth scopes are read-only too, so even buggy code would get `OAUTH_SCOPE_MISMATCH` from Zoho. That's three independent layers.

**Q: Why allowlists for PII instead of removing email/phone?**
A blocklist fails open: if Zoho adds a new `personal_mobile` field next year, it leaks. An allowlist fails closed: anything I didn't explicitly approve is dropped.

**Q: What if someone connects production by mistake?**
The tool detects the environment from the `api_domain` Zoho returns (sandbox/developer subdomains). If it doesn't match what the user declared, it revokes the token immediately and saves nothing. Production is blocked entirely until the production safeguards phase.
