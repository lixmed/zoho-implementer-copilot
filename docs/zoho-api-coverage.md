# Zoho CRM API Coverage Check (Phase 0)

_Researched 2026-11-22 against the official Zoho CRM **v8** API docs (the newest version the docs show; no v9 found)._
_Base URL pattern: `{api_domain}/crm/v8/...`. Scope strings are copied from the docs._

**How to read the "Status" column**
- ✅ **Documented**: public endpoint, docs page read.
- 🟡 **Partial**: possible, but with documented restrictions, or done through a different endpoint.
- ❌ **Not found**: no public endpoint in the v8 API reference. Treat it as UI-only until proven otherwise.
- ❓ **Unverified**: the docs are silent or contradict each other. We must test it in the dev org before relying on it.

---

## 1. Read (Level 1, Planner)

| Action | Status | Endpoint | Scope | Notes |
|---|---|---|---|---|
| Modules | ✅ | `GET /settings/modules`, `GET /settings/modules/{api_name}` | `ZohoCRM.settings.modules.READ` | 1 credit |
| Fields | ✅ | `GET /settings/fields?module=X` | `ZohoCRM.settings.fields.READ` | 1 credit. The docs contradict themselves on whether picklist values are included. Use the picklist endpoint below. |
| Picklist values | ✅ | `GET /settings/fields/{field_id}/pick_list_values?module=X` | `ZohoCRM.settings.fields.READ` | Returns used and unused options |
| Layouts | ✅ | `GET /settings/layouts?module=X` | `ZohoCRM.settings.layouts.READ` | Sections, fields, required flags, status |
| Related lists | ✅ | `GET /settings/related_lists?module=X` | `ZohoCRM.settings.related_lists.READ` | Read-only |
| Workflow rules | ✅ | `GET /settings/automation/workflow_rules` | `ZohoCRM.settings.workflow_rules.READ` | Max 200 per page |
| Workflow capabilities | ✅ | `GET /workflow_configurations?module=X` (no `/settings` prefix) | `ZohoCRM.settings.workflow_rules.READ` | Lists the triggers, action types and per-action limits available in this org |
| Blueprints (config) | ✅ | `GET /settings/blueprints` | `ZohoCRM.settings.blueprint.READ` | States and transitions |
| Custom functions | ✅ | `GET /settings/functions` | `ZohoCRM.settings.functions.READ` | Shows `category` and `rest_api_mode` (Oauth / ZAPI / None). Returns `FEATURE_NOT_SUPPORTED` if the edition lacks Functions. |
| Connections | ❌ | — | — | No list endpoint. We find link names by parsing function code, or the user enters them. |
| Roles | ✅ | `GET /settings/roles` | `ZohoCRM.settings.roles.READ` | 1 credit |
| Profiles | ✅ | `GET /settings/profiles[/{id}]` | `ZohoCRM.settings.profiles.READ` | Permission details only on single-profile GET |
| Users | ✅ | `GET /users` | `ZohoCRM.users.READ` | ⚠️ Contains PII (names, emails). Mask before it reaches the LLM. |
| Validation rules | ❌ | — | — | Only a `validation_rule` key inside layout field JSON |
| Custom buttons / links | ❌ | — | — | |

**Minimum Level 1 scope set** (metadata only, no record access):
`ZohoCRM.settings.modules.READ, ZohoCRM.settings.fields.READ, ZohoCRM.settings.layouts.READ, ZohoCRM.settings.related_lists.READ, ZohoCRM.settings.workflow_rules.READ, ZohoCRM.settings.blueprint.READ, ZohoCRM.settings.functions.READ, ZohoCRM.settings.roles.READ, ZohoCRM.settings.profiles.READ, ZohoCRM.users.READ`

Level 1 deliberately requests **no `ZohoCRM.modules.*` scope**, so the tool cannot read customer records. This enforces constraint #6 (no PII to the LLM) through OAuth, not just through our code.

---

## 2. Create / update (Level 2, Builder)

| Action | Status | Endpoint | Scope | Limits / gotchas |
|---|---|---|---|---|
| Create custom field | ✅ | `POST /settings/fields?module=X` | `ZohoCRM.settings.fields.CREATE` | Max 5 per call. **10 credits per field.** Fields per module: Standard 10, Professional 155, Enterprise 300, Ultimate 500. Not placed on a layout → goes to Standard layout, "Unused" elsewhere. |
| Update custom field | 🟡 | `PATCH /settings/fields/{id}?module=X` | `ZohoCRM.settings.fields.UPDATE` | **Cannot change data type or API name** via API. Textarea length is fixed. |
| Add / rename picklist option | 🟡 | `PATCH /settings/fields/{id}?module=X` with `pick_list_values` | `ZohoCRM.settings.fields.UPDATE` | Removing an option = mark unused through the layout update |
| Create custom module | 🟡 | `POST /settings/modules` | `ZohoCRM.settings.modules.CREATE` | **Enterprise or higher.** **500 credits.** One per call. No subform or many-to-many modules. |
| Update custom module | 🟡 | `PUT /settings/modules/{id}` | `ZohoCRM.settings.modules.UPDATE` | Labels and profiles only |
| Create layout | ❌ | — | — | UI only |
| Update layout | ✅ | `PATCH /settings/layouts/{id}?module=X` | `ZohoCRM.settings.layouts.UPDATE` | Max 5 field actions and 5 new sections per call. Sets `required` and places fields. |
| Activate / deactivate layout | ✅ | `POST` / `DELETE /settings/layouts/{id}/actions/activate` | `...layouts.CREATE` / `...layouts.DELETE` | Deactivation needs `transfer_to` |
| Workflow rule create / update | ✅ | `POST` / `PUT /settings/automation/workflow_rules[/{id}]` | `ZohoCRM.settings.workflow_rules.CREATE` / `.UPDATE` | One rule per call. Max 10 conditions. You cannot change the trigger type or module after creation. |
| Field-update action | ✅ | `POST /settings/automation/field_updates` | `ZohoCRM.settings.automation_actions.CREATE` | Static values only. Create the action first, then reference its id from the rule. |
| Email-notification action | ✅ | `POST /settings/automation/email_notifications` | `ZohoCRM.settings.automation_actions.CREATE` | The **email template must already exist** (we found no template-create endpoint). |
| Task action | ✅ | `POST /settings/automation/tasks` | `ZohoCRM.settings.automation_actions.CREATE` | |
| Webhook action | ✅ | (Actions API) | `ZohoCRM.settings.automation_actions.*` | Listed in the Actions API announcement. Detail page not read. |
| **Attach a function to a workflow rule** | ❓ | — | — | `workflow_configurations` lists a `functions` action type ("Deluge", limit 1 per rule), but the **create-rule docs do not show how to attach it by id**. Test this in the dev org. Until proven, treat it as a manual step. |
| Blueprint create / edit | ❌ | — | — | Only record transitions (`PUT /{module}/{id}/actions/blueprint`) exist. Config is UI only. |
| Validation rules | ❌ | — | — | UI only |
| Custom buttons | ❌ | — | — | UI only |
| Related lists create / update | ❌ | — | — | UI only |
| Create role | ✅ | `POST /settings/roles` | `ZohoCRM.settings.roles.CREATE` | |
| Create profile | 🟡 | `POST /settings/profiles/{id}/actions/clone` | `ZohoCRM.settings.profiles.CREATE` | **Clone only.** Then `PUT` to toggle permissions. |

### Danger tier (delete endpoints that exist)

| Endpoint | Side effect |
|---|---|
| `DELETE /settings/fields/{id}?module=X` | Deletes a custom field **and its data**. No undo documented. Blocked if a workflow uses the field. |
| `DELETE /settings/layouts/{id}?transfer_to=` | Users are moved to another layout |
| `DELETE /settings/roles/{id}?transfer_to_id=` | Users are moved to another role |
| `DELETE /settings/profiles/{id}?transfer_to=` | Users are moved to another profile |
| `DELETE /settings/automation/workflow_rules?ids=` | Up to 10 rules per call |
| `DELETE /settings/global_picklists/{id}` | Runs as an asynchronous job |
| Module delete | ❌ No endpoint (good) |

> **Rollback implication:** "rollback" of a created field = deleting the field, which destroys any data entered since. Rollback is therefore only safe when nothing has written data yet (typically in the sandbox, or immediately after creation). The tool must check the record count before a rollback delete, using a count call that returns a number and no records. A field **update** cannot be fully undone either when it involves type or API-name changes, but the API blocks those changes anyway.

---

## 3. Deluge custom functions: the debug-loop questions

| Question | Answer | Source |
|---|---|---|
| (a) Create / update function code via API? | ✅ **Yes.** `POST /settings/functions` (multipart: `metadata` JSON plus code, or inline `_code`), `PUT /settings/functions/{id_or_api_name}`. Scope `ZohoCRM.settings.functions.CREATE` / `.UPDATE`. Deluge is **auto-published**. A compile failure returns `COMPILATION_ERROR` **with line and character position**. Limit: 20,000 functions per org. Needs the "Manage Automation" permission. | create-function.html, update-function.html |
| (b) Invoke via API? | 🟡 **Yes, for REST-API-enabled functions.** `…/crm/v8/functions/{api_name}/actions/execute` with OAuth (`Authorization: Zoho-oauthtoken`) or an API key in the URL. Arguments arrive as `crmAPIRequest`; the function returns `crmAPIResponse`. **Enabling REST mode is done in the UI**; we found no API for it. ❓ The scope needed for execute is not documented. Timeout for REST functions: **10 s**. | create-serverless.html, limits-quotas.html |
| (c) Read execution errors / logs? | ❌ **No logs API found.** Logs are only in Setup → Developer Hub → Functions. The error format for a runtime error from execute is undocumented. | functions overview |
| Timeouts | REST / button / validation: 10 s · Automation (workflow, blueprint, approval): 30 s · Schedule: 15 min · 200,000 statements · 10 MB response | limits-quotas.html |
| Function credits | Standard / Professional: functions only via Extensions. Enterprise: 20,000 + 500 × users (max 400,000). Ultimate: 20,000 + 1,000 × users. | limits-quotas.html |

### What this means for the debug loop (better than expected)

1. **No paste needed for code.** Because create/update exists, the tool pushes the code itself (after approval) and Zoho compiles it. `COMPILATION_ERROR` with line and column is our first automatic feedback loop.
2. **Runtime testing.** Logs are unreadable via API, so the function must report its own result:
   - Generate the business logic as a **standalone function returning a structured map** (`{status, steps, errors}`). This is testable through `actions/execute` once REST mode is turned on: a one-time UI click, which goes on the checklist.
   - The **automation function** (the one a workflow calls) becomes a thin wrapper that calls the core logic. The wrapper is tested with **behavior tests**: create a test record in the sandbox, wait, read it back, compare, then delete. Behavior tests need `ZohoCRM.modules.{module}.ALL` on the **sandbox** org only, using fake data.
3. **Attaching the function to the workflow** stays a checklist step until ❓ is resolved.

---

## 4. Connections

| Question | Answer |
|---|---|
| Create via API? | ❌ **No.** UI only: Setup → Developer Hub → Connections. The client's admin must authorize it there. |
| List via API? | ❌ Not found |
| Referenced in Deluge? | By **link name**: `invokeurl [ url: "...", type: GET, connection: "link_name" ]`. Link name rules: starts lowercase, `[a-z0-9_]`, ≤ 50 characters. |

→ Connections are always a **handoff item**: `connections.md` gives the service, connection name, exact link name (validated against the generated code) and the minimum scopes, each with a reason. This is good for security anyway: the client admin authorizes third-party access themselves.

---

## 5. Sandbox

| Question | Answer |
|---|---|
| Editions | Free / Standard / Professional: **none**. Enterprise: 3 configuration-only + 2 configuration-and-data. Ultimate: 7 + 3. (From the comparison page; read in translation, so re-check the numbers.) |
| API access to a sandbox | ✅ The normal APIs work against `sandbox.zohoapis.{dc}` with a token issued for the **sandbox environment**. Tokens are environment-specific and cannot cross between sandbox and production. |
| API to create / refresh / deploy a sandbox | ❌ Not found. Create, refresh and "deploy to production" are UI only. |
| Developer edition | Zoho still advertises a free "developer edition" and a developer environment domain `developer.zohoapis.{dc}`. Its limits page dates from 2018 (10 users, 10k records, Functions 100 calls/day, 10 workflow rules per module). ❓ Whether it has a sandbox is unclear. |

**Implication for "sandbox first":** each environment is a **separate org profile** with its own token (`sandbox` / `production` / `developer`). Writes default to the sandbox profile. Promotion to production has two possible routes, a decision for Phase 7:
- (A) the client uses Zoho's own sandbox deploy UI (we generate the checklist), or
- (B) we replay the approved change set against the production profile (production flag plus typed confirmation).

For development we use the free developer org as the "sandbox" target.

---

## 6. API credits and rate limits

| Edition | Credits / rolling 24 h | Max | Concurrency |
|---|---|---|---|
| Free | 5,000 | 5,000 | 5 |
| Standard | 50,000 + 250 × users | 100,000 | 10 |
| Professional | 50,000 + 500 × users | 3,000,000 | 15 |
| Enterprise / Zoho One | 50,000 + 1,000 × users | 5,000,000 | 20 |
| Ultimate / CRM Plus | 50,000 + 2,000 × users | unlimited | 25 |

- Sub-concurrency is 10 for heavy calls (COQL, composite, bulk inserts).
- No per-minute limit. Over the limit you get `TOO_MANY_REQUESTS`.
- The `X-API-CREDITS-REMAINING` header appears once usage reaches 50%.
- **Expensive calls for us:** create module 500, create field 10 per field, record count 50; metadata reads cost 1.
- A full metadata snapshot of a 30-module org is about 30 × 4 (fields, layouts, related lists, picklists) ≈ 120–200 credits. That is cheap, but **cache it per org** anyway.
- OAuth throttles: max 10 access tokens per refresh token per 10 min, 15 active access tokens per refresh token, 20 refresh tokens per user (the 21st silently kills the oldest), and grant codes valid for about 1–3 minutes (the docs disagree).
- Refresh tokens **never expire** until revoked → they must be stored in the OS keychain.

---

## 7. Data centres and OAuth

| DC | Accounts server | Source |
|---|---|---|
| US | `https://accounts.zoho.com` | multi-dc.html |
| EU | `https://accounts.zoho.eu` | multi-dc.html |
| IN | `https://accounts.zoho.in` | multi-dc.html |
| AU | `https://accounts.zoho.com.au` | multi-dc.html |
| JP | `https://accounts.zoho.jp` | multi-dc.html |
| CA | `https://accounts.zohocloud.ca` | multi-dc.html |
| SA | `https://accounts.zoho.sa` | multi-dc.html |
| UK | `https://accounts.zoho.uk` | accounts multi-dc.html |
| CN | `https://accounts.zoho.com.cn` | CRM multi-dc.html (missing from the live `serverinfo` list) |
| Also live | AE `accounts.zoho.ae`, SG `accounts.zoho.sg` | `https://accounts.zoho.com/oauth/serverinfo` |

- **API base URL:** do **not** hardcode it. The token response returns `api_domain`, and we store it per org.
  Docs confirm `www.zohoapis.com`, `.eu`, `.in` and `.com.cn`. The `.uk`, `.sa`, `.ca` and other API domains are ❓ not listed in the pages read, which is another reason to trust `api_domain`.
- **DC detection:** start authorization at `accounts.zoho.com`. The callback carries `location=` and `accounts-server=`, and we exchange the code at that accounts server. Multi-DC must be enabled for the client in the API Console, and **the client secret differs per DC**.
- **Flow for a local app:** a server-based client with `access_type=offline` (to get a refresh token) plus **PKCE (S256)**. PKCE is documented and recommended for server-based clients.
- ❓ **Localhost redirect URI:** the docs only say it must start with `http://` or `https://`. Test `http://127.0.0.1:<port>/callback` in Phase 1.
- **Revoke:** `POST {accounts-server}/oauth/v2/revoke/token` with `token=`. This also revokes the derived access tokens.
- **Client admin can revoke at any time** from their Zoho account (connected apps). The token is tied to the authorizing user's permissions, so the client's admin should authorize, or a dedicated implementer user with an admin profile should.

---

## 8. What Zoho already offers, and how we position against it

| Zoho offering | What it does | Gap we fill |
|---|---|---|
| **Zoho CRM MCP servers** (Zoho-hosted, 4 servers): Data Insights (read-only records), Data Operations (record CRUD), **Module Customization** (create modules and fields, update layouts), **Workflow & Process Automation** (create/update workflow rules and task actions) | Lets Claude, Cursor and similar clients change CRM config from prompts, using the user's permissions. Calls consume API credits. | No plan or approval step, no diff, snapshot or rollback, no audit log, no sandbox-first policy. Choosing which servers to install is the only permission control. One org per connection (you must re-authenticate to switch). Deluge functions, connections and handoff are not covered. The record servers expose customer data to the LLM. |
| **Zoho MCP** (zoho.com/mcp) | A builder for custom MCP servers across 15+ Zoho apps | Generic tool exposure, not an implementation workflow |
| **Zia Agents / Agent Studio** | No-code agents inside Zoho with 700+ actions. Ask Zia can create workflows. | Lives inside one org and is aimed at end users. It is not a partner's multi-client delivery tool. |
| **Zia in the Deluge editor** | AI code generation, refinement and analysis inside the editor | One function at a time, inside the UI. It is not tied to a requirements plan, offers no checks against live API names, and has no test loop. |

**Positioning:** this is not "AI that can edit Zoho". Zoho now ships that itself. It is an **implementation copilot for Zoho partners**:
1. **Requirements → reviewable plan** (plan.json / plan.md with client questions, scopes and tickets) before anything is touched.
2. **Multi-client org registry, entirely local**: one machine, many client orgs, each revocable, tokens in the keychain.
3. **Change-set safety**: approval gates, sandbox default, typed production confirmation, diff, snapshot, rollback and an audit log.
4. **Metadata-only by design**: no record scopes in Level 1, and masking whenever samples are needed.
5. **Deluge that compiles and passes tests** against real API names, through a deploy → compile → execute → fix loop.
6. **A handoff pack for everything the API cannot do**: connections, validation rules, blueprints, buttons, layouts and function-to-workflow attachment.
7. **Measured quality**: an eval suite against gold plans.

**Open design choice:** our MCP server could call Zoho's hosted MCP servers for some writes instead of the REST API. I recommend **against** it: we would lose control over audit, diff and approval at the call level, and the hosted servers are tied to one org. We call the REST API directly and can mention Zoho's MCP as complementary.

### Chosen differentiator: requirements traceability and change requests (approved 2026-11-23)

Nothing like this appears among the tools Zoho's CRM MCP servers list. It belongs in the **Planner (Phase 2)**, and its scoring feeds the evals (Phase 6).

1. **Source links:** every plan item (field, workflow, function, connection, ticket, question) carries a `source` reference: the exact sentence or passage in the client's requirements document, with document id and offset or quote.
2. **Coverage matrix:** every requirement sentence gets a status: covered by plan items / needs clarification / out of scope (with a reason) / not addressed. "Not addressed" is a planner failure and is shown first.
3. **Client deliverables generated from the plan:**
   - a sign-off doc in plain language (what will be built and why, mapped to their requirements);
   - a UAT test script (steps the client runs to accept each requirement, built from the function tests and workflow conditions).
4. **Change requests:** when the client sends new or changed requirements, the tool re-plans and shows a **diff against the approved plan version**: items added, changed or removed, which already-applied changes are affected, and a time estimate for each delta. Plans are versioned (`plan.v1.json`, `plan.v2.json`, ...), each with an approval record.
5. **Eval hook:** traceability lets the eval suite score "requirements not addressed" and "plan items with no source" (possible hallucinated scope).

Not chosen for now (can revisit later): impact analysis and dependency graph, drift and environment diff, pre-flight checks and org health audit.

---

## 9. Recommendation: what Level 2 automates vs. what is handed off

**Level 2 can automate (via API, after approval):**
- Custom fields: create, plus safe updates (label, length where allowed, tooltip, picklist options)
- Placing fields on existing layouts, required flags, new sections (≤ 5 per call), activating and deactivating layouts
- Custom modules (Enterprise+ only; 500 credits each, so warn first)
- Workflow rules plus field-update, email-notification (given an existing template), task and webhook actions
- Deluge functions: create, update, compile check, and REST execution of standalone functions
- Roles; profiles (clone, then permission toggles)
- Behavior tests in the sandbox (test records created and cleaned up)

**Must be guided handoff (checklists, copy-and-open):**
- **Connections** (always; the client admin authorizes)
- Function → workflow rule attachment (❓ until tested)
- Enabling REST API mode on a function (one click)
- Creating new layouts, validation rules, custom buttons, related lists, blueprints, email templates
- Sandbox creation, refresh and deploy to production (if we choose route A)
- Changing a field's API name or type (impossible via API anyway; usually the answer is "create a new field")

**Items to verify in the dev org during Phase 1 (before we rely on them):**
1. A localhost / 127.0.0.1 redirect URI is accepted.
2. Can `functions` be attached as a workflow action through `POST /settings/automation/workflow_rules`?
3. The scope needed for `functions/{name}/actions/execute` with OAuth, and the response body on a Deluge runtime error.
4. Is a sandbox available on the developer edition, and which `api_domain` comes back for it?
5. The real `api_domain` values returned for each DC we care about.

---

## 10. Verified against a real org (2026-11-26)

Free CRM org on a 15-day Enterprise trial, US DC.

| Question | Result |
|---|---|
| Localhost redirect `http://127.0.0.1:8765/callback` | ✅ Accepted by Zoho |
| Token request format | ✅ form-urlencoded body works |
| PKCE | ✅ Works, **but** verifiers containing `.` or `~` (valid per RFC 7636) intermittently get a generic HTML 400 page *"An error occurred while processing your request"*. We now use base64url characters only. |
| Environment of a free/trial org | `api_domain` is `https://www.zohoapis.com`, `GET /org` returns `"type": "production"` and `license_details.paid: false, paid_type: "free", trial_type: "enterprise"`. Nothing says "developer". The tool's `--env test` accepts it only if unpaid and the user types the org id. |
| All 11 metadata read endpoints | ✅ Work: modules (32), fields, layouts, related lists, picklist values, workflow rules, blueprints, functions, roles, profiles, users (masked) |
| `X-API-CREDITS-REMAINING` header | Not sent at low usage (as documented: only from 50% of the daily limit) |
| Fields without a formula | Return `"formula": {}` |
| **`POST /settings/functions` (create function)** | ❌ `INVALID_REQUEST` ("verify method name, parameter and parameter values") for both inline `_code` and a `.ds` file part, with a request matching the doc sample. `GET` on the same path works. Likely not enabled for this edition or rollout. **The paste-from-handoff path is the reliable route; API push is best-effort.** |
| Deluge declaration for automation functions | ❓ Still unverified (create is unavailable on this org) |

## Sources (all fetched 2026-11-22)
- API reference index: https://www.zoho.com/crm/developer/docs/api/v8/api-references.html
- What's new in v8: https://www.zoho.com/crm/developer/docs/api/v8/whats-new.html
- Metadata: `modules-api.html`, `module-meta.html`, `field-meta.html`, `picklist-values.html`, `layouts-meta.html`, `related-list-meta.html`, `blueprints.html`, `get-roles.html`, `get-profiles.html`, `get-users.html` (all under `/crm/developer/docs/api/v8/`)
- Writes: `create-custom-field.html`, `update-custom-fields.html`, `delete-custom-fields.html`, `create-custom-module-api.html`, `update-custom-module-api.html`, `update-custom-layout.html`, `activate-custom-layout.html`, `deactivate-custom-layout.html`, `delete-custom-layout.html`, `config-workflow.html`, `update-workflow.html`, `delete-workflows.html`, `workflow-configurations.html`, `create-field-update.html`, `create-email-notification.html`, `create-automation-task.html`, `create-role.html`, `create-profile.html`, `update-profile-permission.html`, `update-blueprint.html`
- Functions: `functions-list.html`, `create-function.html`, `update-function.html`; https://www.zoho.com/crm/developer/docs/functions/ ; `functions/limits-quotas.html`; `functions/serverless.html`; `functions/getting-started/quickstart/create-serverless.html`
- Connections: https://www.zoho.com/deluge/help/connections.html
- Limits: https://www.zoho.com/crm/developer/docs/api/v8/api-limits.html ; https://www.zoho.com/crm/developer/docs/edition-limits.html ; https://www.zoho.com/crm/comparison.html
- OAuth / DC: https://www.zoho.com/crm/developer/docs/api/v8/multi-dc.html ; `auth-request.html`; `access-refresh.html`; `token-validity.html`; `scopes.html`; https://www.zoho.com/accounts/protocol/oauth/multi-dc.html ; https://www.zoho.com/accounts/protocol/oauth/web-server-applications.html ; https://www.zoho.com/accounts/protocol/oauth/revoke-refresh-token.html ; https://accounts.zoho.com/oauth/serverinfo
- Sandbox / developer: https://www.zoho.com/crm/developer/sandbox.html ; https://www.zoho.com/crm/developer/developer-edition.html
- Zoho AI: https://www.zoho.com/crm/developer/docs/mcp/overview.html ; https://www.zoho.com/crm/developer/mcp.html ; https://www.zoho.com/mcp/ ; https://www.zoho.com/deluge/ ; https://cxtoday.com/crm/zoho-makes-5-major-moves-to-expand-its-ai-portfolio (third-party)
