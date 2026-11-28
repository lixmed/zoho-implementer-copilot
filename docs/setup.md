# Setup

## 1. Install (one command)
Needs [uv](https://docs.astral.sh/uv/). Python 3.12 is fetched automatically if missing.
```powershell
uv tool install git+https://github.com/lixmed/zoho-implementer-copilot
copilot doctor
```
To try it without a Zoho account: `copilot demo`.

Upgrade later with `uv tool upgrade zoho-implementer-copilot`.

For development (run from a clone):
```powershell
git clone https://github.com/lixmed/zoho-implementer-copilot; cd zoho-implementer-copilot
uv sync
uv run pytest
```

## 2. Register a Zoho API client (once)
1. Open https://api-console.zoho.com (the console for your data centre, e.g. `api-console.zoho.eu`) → **Add Client** → **Server-based Applications**.
2. Homepage URL: `http://127.0.0.1:8765`
3. Authorized Redirect URI: `http://127.0.0.1:8765/callback` (verified to work)
4. Copy the Client ID and Client Secret. Don't paste them anywhere except the next step.

## 3. Store the credentials in the OS keychain
```powershell
copilot client set --dc us
```
Use your data centre: `us`, `eu`, `in`, `au`, `jp`, `ca`, `sa`, `uk`, `ae`, `sg` or `cn`.

## 4. Connect an org
| Org | Command |
|---|---|
| Your own free/trial org | `copilot connect --name "My Test Org" --env test` (you'll type the org id to confirm it's yours) |
| A sandbox | `copilot connect --name "Company Sandbox" --env sandbox` |
| A developer org | `copilot connect --name "Dev Org" --env developer` |

Add `--allow-writes` for Deluge push and the Builder. Add `--allow-test-records --test-modules Leads,Deals` for the test loop. Production is blocked until the production safeguards phase.

## 5. Use it from Claude Code
```powershell
claude mcp add zoho-copilot -- copilot serve
```
Then, for example: *"List my Zoho orgs and show the custom fields on Leads."* or use the `plan_project` and `write_deluge` prompts.

## Everyday commands
```powershell
copilot workspace set C:/Users/you/ZohoProjects
copilot project new "Client" "Project" --org my-org
copilot project ingest client/project
copilot plan approve client/project
copilot changes preview client/project
copilot apply client/project
copilot rollback client/project
copilot deluge loop client/project my_function
copilot audit
```

## Disconnect
```powershell
copilot orgs remove my-org --revoke
```
