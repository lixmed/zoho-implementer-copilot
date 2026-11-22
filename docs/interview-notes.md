# Interview Notes

## Phase 0: API coverage check

**Q: Why did you check API coverage before writing any code?**
Because the architecture depends on it. If functions couldn't be deployed via API, the "debug loop" would be copy-paste only. If connections could be created via API, the security model would change. Checking first told me the real split: fields, layouts, workflows and function code are automatable. Connections, validation rules, blueprints and buttons are UI-only. So the "handoff pack" is a core feature, not a fallback.

**Q: Zoho already has an MCP server that can create fields and workflows. Why build this?**
Zoho's hosted MCP servers give an AI raw write access to one org. A partner needs a delivery process on top of that: requirements → a reviewable plan → approval → sandbox → diff → rollback → audit, across many client orgs, with customer data kept away from the LLM. Zoho's own servers have no plan, diff, rollback or audit step, and their record tools expose PII. I call the REST API directly so that every write passes through my own approval and audit layer.

**Q: How do you keep customer PII away from the LLM?**
First at the OAuth level: the planner requests only `ZohoCRM.settings.*.READ` and `ZohoCRM.users.READ`, never `ZohoCRM.modules.*`, so it *cannot* read records. Second, user names and emails from `/users` are masked before they reach the model. Record scopes are requested only on the sandbox org, for behavior tests that use fake data.

**Q: What stops the agent from deleting a field in production?**
Delete endpoints are in a separate "danger" tier. Sandbox and production are separate org profiles with separate tokens, because Zoho tokens are environment-specific. Production writes need a production flag and a typed confirmation. Before any rollback that deletes a field, the tool checks the record count, because deleting a field destroys its data.

**Q: What does your tool do that Zoho's own MCP can't?**
Zoho's MCP executes changes. My tool delivers projects. The headline feature is traceability: every plan item links back to the exact sentence in the client's requirements, so there's a coverage matrix showing nothing was missed and nothing was invented. When the client changes their mind, the tool diffs the new plan against the approved version and estimates each change, so scope creep is visible and billable. It also generates the client sign-off doc and the UAT script from the same plan. And because the eval suite scores "requirements not addressed" and "items with no source", traceability doubles as a hallucination check.

**Q: How does the tool handle Zoho's data centres?**
It never hardcodes the API URL. Authorization starts at accounts.zoho.com. The callback says which accounts server the user belongs to, and the token response returns `api_domain`. Both are stored per org profile.
