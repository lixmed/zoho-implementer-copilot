# ADR 0003: uv for tooling, OS keychain for secrets

**Status:** accepted (2026-11-23)

## Tooling
- Options: **uv** / pip + venv / Poetry. We chose uv: it gives a lockfile, it is fast, CI uses the same tool, and `uv tool install` provides the one-command install.
- MCP Python SDK **2.x** (`MCPServer`, formerly `FastMCP`).

## Secrets
- Options: **OS keychain** (`keyring`) / an encrypted file / `.env`. We chose the keychain.
- Refresh tokens and per-DC client secrets live only in the keychain. The org registry (`orgs.json`) holds no secrets.
- Access tokens are kept in memory only and refreshed 2 minutes before they expire.
