# Setup (Phase 1)

## 1. Install
```powershell
git clone https://github.com/<you>/zoho-implementer-copilot; cd zoho-implementer-copilot
uv sync
```

## 2. Register a Zoho API client (once)
1. Go to https://api-console.zoho.com (use the console for your data centre) → **Add Client** → **Server-based Applications**.
2. Homepage URL: `http://127.0.0.1:8765`
3. Authorized Redirect URI: `http://127.0.0.1:8765/callback`
4. Copy the Client ID and Client Secret.
5. Optional: enable **Multi-DC** in the client's Settings tab. Each DC then shows its own secret.

## 3. Store the credentials in the OS keychain
```powershell
uv run copilot client set --dc us
```
Add more data centres later with, for example, `--dc us eu`.

## 4. Connect your developer org
```powershell
uv run copilot connect --name "My Dev Org" --env developer
```
A browser opens. Log in, approve, and you're done. Then check:
```powershell
uv run copilot orgs list
uv run copilot snapshot my-dev-org
```

## 5. Use it from Claude Code
```powershell
claude mcp add zoho-copilot -- uv --directory "C:/path/to/zoho-implementer-copilot" run copilot serve
```
Then ask: "List my Zoho orgs and show the custom fields on Leads."

## Disconnect
```powershell
uv run copilot orgs remove my-dev-org --revoke
```
