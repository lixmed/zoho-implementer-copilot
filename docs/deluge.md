# Deluge functions (Phase 3)

## Flow
1. The plan is approved (`copilot plan approve`). Functions come from `function` items in the plan.
2. In Claude Code, use the **`write_deluge`** prompt (or say "write post_hot_lead for acme-realty/onboarding"). The agent then:
   - calls `get_function_brief` for the plan item, the **exact field api_names** (from the org and from fields the plan creates), connections and triggers;
   - looks up syntax live with `search_deluge_docs(query, app)` and `read_deluge_doc(url)`;
   - runs `check_deluge` until it's clean, then `save_deluge` with test cases.
3. **You** push it to the dev or sandbox org:
   ```powershell
   uv run copilot deluge push acme-realty/onboarding post_hot_lead
   ```
   You type the function name to confirm. If Zoho's compiler rejects the code, the line number and position are saved. The agent reads them with `get_push_result`, fixes the code, and you push again.
4. The function is now **saved in Zoho but not attached** to any workflow. Attaching it is a separate, approved step from the handoff checklist.

The org must be connected with write scopes for this:
```powershell
uv run copilot connect --name "My Dev Org" --env developer --allow-writes
```
That adds only `ZohoCRM.settings.functions.CREATE` and `ZohoCRM.settings.functions.UPDATE`.

## Live docs lookup
- Search is per app (`crm`, `books`, `creator`, `desk`, `people`, `projects`, …). Deluge tasks differ between apps.
- How it works: the tool downloads Zoho's public task index for that app (e.g. `/deluge/help/crm-tasks.html`) and ranks the links **on your machine**. The search words are never sent anywhere.
- Only `https://www.zoho.com/deluge/help/…` and `/<app>/help|developer/…` pages can be fetched. Redirects must stay inside that allowlist.
- Pages are cached for 7 days in the app data folder.

## Static checks (`check_deluge`)
| Check | Level |
|---|---|
| Declaration `<returntype> <category>.<name>(…)` matches the plan's name and category; return type allowed for the category | error |
| Balanced brackets (strings and comments ignored) | error |
| Hard-coded tokens, keys, passwords, `Authorization` headers, Zoho token literals | error |
| `invokeurl` without `connection:`, or using a connection the plan item doesn't declare | error |
| `zoho.crm.*` on a module that doesn't exist; plan `uses_fields` that don't exist | error |
| `.get("X")` / `.put("X")` names that aren't fields of the modules used | warning |
| Hard-coded record ids (they differ between sandbox and production) or email addresses | warning |
| Values read from records that are never null-checked | warning |
| No `info` logging; standalone functions without a `"status"` result | warning |

Zoho's own compiler is the final judge of syntax. Its errors come back on push with line and position.

## Handoff files (in the project folder)
- `handoff/functions/<name>.dg`: a header with the menu path, category, arguments, connections, trigger and "NOT attached automatically", followed by the code.
- `handoff/functions/<name>.tests.json`: test cases, used by the Phase 5 test loop.
- `handoff/functions/<name>.push.json`: the last push result.
- `handoff/connections.md`: every connection with its exact link name and minimum scopes with reasons.

## Audit
Every create or update call is in the append-only audit log (SQLite triggers block UPDATE and DELETE). The log holds who, when, which org and environment, plan version, request metadata (the code length, not the code itself), the response, and the before-state for updates.
```powershell
uv run copilot audit
```
