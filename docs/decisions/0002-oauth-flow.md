# ADR 0002: OAuth, server-based client, PKCE, localhost redirect

**Status:** accepted (2026-11-23). The localhost redirect is still to be verified against a real org.

## Options
1. **Server-based client** with redirect `http://127.0.0.1:8765/callback`, PKCE (S256) and `access_type=offline`.
2. Self Client: grant codes pasted by hand within about 3 minutes, done separately for each org.
3. Support both from the start.

## Decision
Option 1, with Self Client as the documented fallback if Zoho rejects a localhost redirect.

## Details
- Authorization starts at `accounts.zoho.com`. The callback's `accounts-server` is checked against an allowlist of Zoho data centres before any secret is sent to it.
- `state` is compared in constant time to prevent CSRF.
- The callback server binds to `127.0.0.1` only and handles a single request.
- `api_domain` comes from the token response and is never hardcoded.
- The environment (production / sandbox / developer) is detected from `api_domain`. If it doesn't match what the user declared, the token is revoked and nothing is saved.
