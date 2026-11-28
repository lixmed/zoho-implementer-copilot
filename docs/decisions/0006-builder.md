# ADR 0006: Builder, change sets, soft rollback, stop on failure

**Status:** accepted (2026-11-28)

## Decisions (made by the user)
1. **Scope: core plus custom modules.** Fields, picklists, layout placement, workflow rules with field-update and task actions, and custom modules (warned: Enterprise+, 500 credits).
2. **Rollback is soft by default.** Hide or deactivate, never delete. `--hard` deletes only after a typed `DELETE`. The tool has no record scopes, so it can't check whether a field already holds data, and deleting a field deletes that data.
3. **Stop at the first failure and offer rollback** of what was applied.

## Design
- **Plan + live org → change set.** It's an ordered list of operations (modules → fields → picklists → layouts → actions → workflows), each holding its exact request and undo info. Ids that only exist after an earlier step use `$op:<id>` placeholders, resolved at apply time.
- **Structured plan items.** Free-text criteria can't be applied safely, so `WorkflowItem.conditions`, `WorkflowAction.field_update/task` and `LayoutItem.field_placements` were added. Without them an item stays a handoff step, and the validator says why.
- **Idempotent.** Items already in the org are skipped.
- **Same gates as Deluge push:** approved and unchanged plan, developer/sandbox/test org only, write scopes, typed confirmation. No apply or rollback tool over MCP.
- **Snapshots** of touched modules before and after, plus a diff. Every call goes to the append-only audit log.

## Trade-offs
- Email-notification and webhook actions stay manual. I couldn't confirm their request formats (the template key, the webhook body) from the docs, and I won't guess.
- A few request shapes are inferred rather than shown in a doc sample (listed in docs/builder.md). They'll be confirmed on the company sandbox.
- Soft rollback leaves residue (unused fields and inactive rules). That's deliberate: residue is recoverable, deleted data isn't.
