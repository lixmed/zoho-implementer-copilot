# ADR 0001: Agent client, MCP first, own loop later

**Status:** accepted (2026-11-23)

## Context
The agent can be an existing MCP client (Claude Code, Claude Desktop), our own loop on the Claude API, or both.

## Options
1. MCP clients only: least code, but evals would have to be run by hand.
2. Own loop only: full control, but more code before the first demo.
3. **Both, staged:** an MCP server now, our own Claude API loop in Phase 6 for scripted eval runs.

## Decision
Option 3. All Zoho access lives in the MCP server, so both agents share the same safety layer (tiers, approvals, audit). Default models for our own loop: `claude-sonnet-5-5`, with `claude-opus-5-5` for complex planning.

## Consequences
Safety rules must live in the server, never in prompts, because we don't control third-party clients.
