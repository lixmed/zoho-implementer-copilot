# Builder (Phase 4)

Applies an **approved** plan to a developer, sandbox or test org through the Zoho API, with a preview, typed confirmation, before/after snapshots, an audit trail, and rollback.

## Flow
```powershell
uv run copilot changes preview acme-realty/phase-1   # read-only: what would change, credits, what's skipped
uv run copilot apply acme-realty/phase-1             # shows the preview, you type the project slug
uv run copilot rollback acme-realty/phase-1          # soft by default
uv run copilot changes list acme-realty/phase-1
```
The agent can call `preview_changes` over MCP. **Applying and rolling back are CLI-only.**

The org must be connected with write scopes (`--allow-writes`). Phase 4 adds: modules.CREATE, fields.CREATE/UPDATE/DELETE, layouts.UPDATE, automation_actions.CREATE/DELETE, workflow_rules.CREATE/UPDATE/DELETE. DELETE scopes are only used by `rollback --hard`.

## What gets applied
| Plan item | Applied when | API operations |
|---|---|---|
| `module` | always (Enterprise+, 500 credits) | `POST /settings/modules` |
| `field` | field doesn't exist yet | `POST /settings/fields` (with `layout_associations`), then a layout PATCH per `placement` |
| `picklist_change` | values not already present | `PATCH /settings/fields/{id}` (add/rename); retire = layout keep-list |
| `layout` (edit) | has structured `field_placements` | `PATCH /settings/layouts/{id}`: move field to section, set required |
| `workflow` | structured `conditions`, trigger create/edit/create_or_edit/delete, only `field_update`/`task` actions with specs | actions first (`POST …/field_updates`, `…/tasks`), then `POST …/workflow_rules` referencing their ids |
| `workflow` with a `function` action | the function is already in the org (push it first) | **EXPERIMENTAL.** The rule is created **inactive** (`status.active=false` in the create body) with `{"id": <function id>, "type": "functions"}` as the action. Zoho's workflow metadata lists `functions` as a valid action, but the create docs show no sample. Check the argument mapping in Zoho before activating. |
| any `workflow` with `"active": false` | always | created inactive |
| everything else | never | handoff pack (email notifications need an existing template; webhooks, new layouts, validation rules, buttons, blueprints) |

Items already present in the org are **skipped**, so running `apply` twice is safe.

## Change sets
Each apply creates `changesets/cs-<timestamp>/` in the project folder:
- `changeset.json`: every operation with its request, status, created id and undo info
- `changes.md`: the human-readable table (the same one shown before you confirm)
- `before.json` / `after.json`: metadata snapshots of the touched modules, plus workflow rules
- `diff.json`: fields and workflows added or removed

Every call, success or failure, is also in the append-only audit log (`copilot audit`).

## Failure handling
Apply stops at the **first failure**. Remaining operations are marked `not_run`, and you're offered a rollback of what was already applied (type `ROLLBACK`).

## Rollback
| Created by the change set | Soft (default) | `--hard` (type `DELETE`) |
|---|---|---|
| Field | moved to *Unused Items* in every layout (data kept) | **deleted, including its data** |
| Picklist values added or retired | layout keep-list restores the previous options | same |
| Picklist rename | renamed back | same |
| Field placement / required | restored to the previous section and required flag (or hidden if it wasn't in the layout) | same |
| Workflow rule | deactivated | deleted |
| Field-update / task action | kept (unused once the rule is off) | deleted |
| Custom module | **manual**: Zoho has no module delete API | manual |

## Verified vs. inferred (from the v8 doc samples)
- **Verified by a doc sample:** field create bodies (text, picklist, lookup, `layout_associations`), field PATCH with `pick_list_values`, layout PATCH (`sections[].fields[]`, `required`, `_delete.permanent=false`, picklist keep-list), field-update and task action bodies, workflow rule body (`execute_when`, `conditions[].criteria_details.criteria`, `instant_actions.actions[{id,type}]`), deactivate via `status.active`, field delete, module create body.
- **Inferred, to confirm on the company sandbox:** placing an *existing* field into another section by listing its id in that section; comparators `not_equal` and `less_than`; the task due-date `trigger_field` for edit triggers (`Modified_Time`).
