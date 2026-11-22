# Zoho Implementer Copilot

A local AI copilot for Zoho partners. It turns client requirements into Zoho CRM implementations, and a human approves every step.

> Status: **Phase 2**. OAuth and read-only metadata tools over MCP, plus a **Planner** that turns
> requirements (.md, .txt, .eml, .docx, .pdf) into a versioned plan. Every plan item is traced back
> to the client's own sentence, and change requests are diffed against the approved version.
> See [docs/planner.md](docs/planner.md).

## What it does (so far)
- Connects a Zoho CRM org through Zoho's official OAuth (server-based client, PKCE, localhost redirect). Supports every Zoho data centre.
- Stores refresh tokens and client secrets in the OS keychain only. No secrets are written to disk.
- Exposes **read-only, metadata-only** tools over MCP (modules, fields, layouts, workflows, functions, roles, profiles, masked users). Any MCP client can use them: Claude Code, Claude Desktop, and others.
- Requests no record scopes in this phase, so customer data cannot reach the LLM.

## Quick start
See [docs/setup.md](docs/setup.md). To test without spending anything (or without any Zoho account at all), see [docs/testing.md](docs/testing.md).

## Docs
- [Zoho API coverage](docs/zoho-api-coverage.md)
- [Security model](docs/security-model.md)
- [Decisions (ADRs)](docs/decisions/)
- [Interview notes](docs/interview-notes.md)
