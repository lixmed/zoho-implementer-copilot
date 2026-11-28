# ADR 0007: Deluge test loop

**Status:** accepted (2026-11-28)

## Decisions (made by the user)
1. **Invoke via OAuth, with an API-key URL fallback.** The execute endpoint is documented, but its OAuth scope isn't. The likely scope is requested behind a flag, and the fallback URL is validated and stored in the keychain.
2. **Core + wrapper** for automation functions. All logic lives in a standalone `<name>_core(record_id)` that the loop can execute directly, so no workflow attachment is needed to test it.
3. **Test records are opt-in and test-org only.** Record scopes are added per module with `--allow-test-records`. Records are fake, created with automation suppressed, and always deleted.

## Design
- A one-shot runner (`deluge test`) plus a human-started **loop** (`deluge loop`). The loop detects the agent's fix by **code content** (a hash compared with the tested code), shows a diff, and re-pushes only after a typed confirmation. That keeps "no write without approval" while removing the copy-paste.
- Attempt history per function gives the **first-placement pass rate** metric from the original spec.
- Setup problems (missing scope, function not in the org, REST not enabled) are reported as setup problems and don't count as test failures in the metric.

## Trade-offs
- Polling a file is simple and works with any MCP client, but it relies on the agent saving through `save_deluge`.
- Several Zoho details are inferred and listed in docs/test-loop.md. The company sandbox run will settle them.
