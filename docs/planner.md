# Planner (Phase 2)

Turns client requirements into a versioned, traceable implementation plan. Nothing is applied to Zoho here.

## Workflow
```powershell
uv run copilot workspace set C:/Users/you/ZohoProjects
uv run copilot project new "Acme Realty" "Phase 1" --org my-dev-org
# drop the client's .md / .txt / .eml / .docx / .pdf files into the project's requirements/ folder
uv run copilot project ingest acme-realty/phase-1
```
Then in Claude Code (with the MCP server added, see setup.md), use the **`plan_project`** prompt, or just say:
> "Draft the implementation plan for acme-realty/phase-1."

Claude reads the requirements and org metadata, drafts the plan, runs `check_plan` until it's clean, then calls `save_plan`.
**You** review `plans/plan.v1.md` and approve:
```powershell
uv run copilot plan approve acme-realty/phase-1
uv run copilot plan export acme-realty/phase-1 signoff uat
```

## Change requests
Add the client's new email or doc to `requirements/` (don't edit old files; that would shift unit ids). Ask Claude to revise the plan, then:
```powershell
uv run copilot plan diff acme-realty/phase-1
```
This compares the latest version against the last **approved** version and writes `exports/change-request-vA-to-vB.md` with the items added, removed and changed, and the effort delta.

## How traceability works
- Every sentence or bullet of every requirement file becomes a **unit** with an id like `kickoff:4`.
- Every plan item, client question and developer ticket must cite unit ids in `sources`.
- **The server computes coverage** from those citations: covered / needs clarification / out of scope (with a reason) / not addressed.
  The LLM can't claim coverage; it has to cite. "Not addressed" is an **error**, so a plan that silently drops a requirement can't be saved.
- `delivery` (api / handoff / decision) is derived from the Phase 0 API coverage table, so the plan can't promise automation Zoho doesn't offer. An item blocked by an open client question becomes `decision`.

## What validation checks
- Schema: types, ids, link-name format, every scope has a reason.
- Traceability: sources exist, every unit is accounted for, cross-references (`depends_on`, `blocks`, `related_items`) resolve.
- Against the live org: modules exist, new fields don't already exist, every `Module.Field` referenced by workflows and functions exists in the org or in this plan.
- Warnings: custom modules need Enterprise+, email notifications need an existing template, `.ALL` scopes, connections the plan doesn't create.

## Files
`plans/plan.vN.json` (machine) · `plans/plan.vN.md` (you) · `plans/approvals.json` (who, when, SHA-256) · `exports/` (sign-off, UAT, change requests).
