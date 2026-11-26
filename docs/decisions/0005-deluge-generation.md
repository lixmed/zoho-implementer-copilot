# ADR 0005: Deluge generation, live docs, static checks, gated push

**Status:** accepted (2026-11-26)

## Decisions (made by the user)
1. **Knowledge comes from live lookups in the official docs, per app**, not a frozen local copy. Deluge tasks differ between CRM, Books, Creator and the other apps, and the docs change.
   - Implementation: rank links from Zoho's public per-app task index **locally**. No search engine and no API key, and the query never leaves the machine. Fetches are allowlisted to zoho.com doc paths and cached for 7 days.
2. **Functions are saved in the dev or sandbox org, never attached to a workflow without approval.**
   - Attaching via API is unverified (Phase 0), and it's the step that makes code run on real records. It stays a handoff step.

## Design
- The agent drafts in Claude Code. The server owns the brief (exact api_names), the checks and the files.
- **No push tool over MCP.** `copilot deluge push` is a CLI command. It requires an approved, unedited plan containing the function, a developer or sandbox org connected with function write scopes, clean static checks, and the typed function name.
- A separate `ZohoWriteClient`: the only code that writes to Zoho. It refuses production, and every call goes to the append-only audit log.
- Write scopes are opt-in (`connect --allow-writes`) and limited to `settings.functions.CREATE/UPDATE`.

## Trade-offs
- The static checks are regex heuristics, not a parser. They're tuned to be conservative: errors only for things that are certainly wrong or dangerous. Zoho's compiler remains the final syntax check.
- The declaration keywords for non-standalone categories follow the documented pattern but are only confirmed by a Zoho sample for `standalone`. A wrong keyword shows up as a `COMPILATION_ERROR` on the first push.
