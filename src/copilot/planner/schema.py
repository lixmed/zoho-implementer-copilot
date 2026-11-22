"""plan.json schema. The LLM fills this in; the server validates it.

Design rules:
- Every item, question and ticket cites `sources`: requirement unit ids (traceability).
- Coverage is COMPUTED by the server from those citations, never claimed by the LLM.
- `delivery` (api / handoff / decision) is DERIVED from the Phase 0 coverage table,
  so the plan can't promise API automation Zoho doesn't offer.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ItemId = Annotated[str, Field(pattern=r"^P-\d{3,}$", description="e.g. P-001; keep ids stable")]
SourceIds = Annotated[
    list[str],
    Field(min_length=1, description="requirement unit ids this item comes from, e.g. 'kickoff:3'"),
]

FieldType = Literal[
    "text", "textarea", "email", "phone", "picklist", "multiselectpicklist", "date",
    "datetime", "integer", "bigint", "double", "currency", "percent", "boolean", "lookup",
    "website", "autonumber", "formula", "fileupload", "rollup_summary", "multi_user_lookup",
]  # fmt: skip


class _Item(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: ItemId
    title: str
    rationale: str = Field(description="why, in terms the client would understand")
    sources: SourceIds
    depends_on: list[str] = Field(default_factory=list)
    estimate_minutes: int = Field(ge=0, description="implementer effort to build and test")
    acceptance_test: str | None = Field(
        default=None, description="how the client verifies it in UAT, one or two steps"
    )


class LayoutPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    layout: str = "Standard"
    section: str
    required: bool = False


class FieldItem(_Item):
    kind: Literal["field"] = "field"
    module: str = Field(description="module api_name, existing or created in this plan")
    field_label: str
    api_name: str = Field(description="proposed api_name; Zoho derives it from the label")
    data_type: FieldType
    picklist_values: list[str] = Field(default_factory=list)
    lookup_module: str | None = None
    length: int | None = None
    placement: list[LayoutPlacement] = Field(default_factory=list)


class PicklistChangeItem(_Item):
    kind: Literal["picklist_change"] = "picklist_change"
    module: str
    field_api_name: str
    add_values: list[str] = Field(default_factory=list)
    rename_values: dict[str, str] = Field(default_factory=dict)
    retire_values: list[str] = Field(default_factory=list)


class ModuleItem(_Item):
    kind: Literal["module"] = "module"
    singular_label: str
    plural_label: str
    api_name: str
    profiles: list[str] = Field(default_factory=lambda: ["Administrator"])


class LayoutItem(_Item):
    kind: Literal["layout"] = "layout"
    module: str
    layout: str
    create_new: bool = Field(description="true = new layout (UI only); false = edit existing")
    changes: list[str] = Field(description="sections/fields to add, move, make required")


class WorkflowAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["field_update", "email_notification", "task", "webhook", "function"]
    details: str
    uses_fields: list[str] = Field(default_factory=list, description="'Module.Field_Api_Name'")


class WorkflowItem(_Item):
    kind: Literal["workflow"] = "workflow"
    module: str
    trigger: Literal["create", "edit", "create_or_edit", "field_update", "date", "delete"]
    criteria: str = Field(description="plain-language condition, with field api_names")
    criteria_fields: list[str] = Field(default_factory=list, description="'Module.Field'")
    actions: list[WorkflowAction] = Field(min_length=1)


class FunctionItem(_Item):
    kind: Literal["function"] = "function"
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    category: Literal[
        "automation", "button", "standalone", "validation", "related_list", "schedule"
    ]
    purpose: str
    arguments: dict[str, str] = Field(
        default_factory=dict, description="arg name -> source, e.g. 'deal_id': 'Deals.id'"
    )
    uses_fields: list[str] = Field(default_factory=list, description="'Module.Field_Api_Name'")
    connections: list[str] = Field(default_factory=list, description="connection link names")


class ScopeReason(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: str
    reason: str


class ConnectionItem(_Item):
    kind: Literal["connection"] = "connection"
    service: str = Field(description="e.g. 'Zoho Books', 'Google Sheets', 'Custom: Acme API'")
    connection_name: str
    link_name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,49}$")
    scopes: list[ScopeReason] = Field(min_length=1, description="minimum scopes, each justified")


class UiOnlyItem(_Item):
    """Settings with no public API (validation rules, buttons, blueprints, related lists...)."""

    kind: Literal["validation_rule", "custom_button", "blueprint", "related_list", "other_ui"]
    module: str | None = None
    steps_hint: str = Field(description="what to configure; the handoff pack turns it into clicks")


PlanItem = Annotated[
    FieldItem | PicklistChangeItem | ModuleItem | LayoutItem | WorkflowItem | FunctionItem
    | ConnectionItem | UiOnlyItem,
    Field(discriminator="kind"),
]  # fmt: skip


class ClientQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^Q-\d{3,}$")
    question: str
    why_it_matters: str
    options: list[str] = Field(default_factory=list, description="suggested answers, if any")
    blocks: list[str] = Field(default_factory=list, description="plan item ids waiting on this")
    sources: SourceIds


class DevTicket(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^T-\d{3,}$")
    title: str
    description: str
    acceptance_criteria: list[str] = Field(min_length=1)
    related_items: list[str] = Field(default_factory=list)
    estimate_hours: float = Field(ge=0)
    sources: SourceIds


class OutOfScope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    unit_id: str
    reason: str = Field(description="e.g. 'greeting', 'already exists in org', 'not a CRM need'")


class PlanDraft(BaseModel):
    """What the LLM submits."""

    model_config = ConfigDict(extra="forbid")
    summary: str = Field(description="3-5 sentences, client-readable")
    items: list[PlanItem] = Field(default_factory=list)
    questions: list[ClientQuestion] = Field(default_factory=list)
    tickets: list[DevTicket] = Field(default_factory=list)
    out_of_scope: list[OutOfScope] = Field(default_factory=list)


Delivery = Literal["api", "handoff", "decision"]
CoverageStatus = Literal["covered", "needs_clarification", "out_of_scope", "not_addressed"]


class CoverageEntry(BaseModel):
    unit_id: str
    status: CoverageStatus
    refs: list[str] = Field(default_factory=list)
    note: str | None = None


class Issue(BaseModel):
    level: Literal["error", "warning"]
    ref: str | None = None
    message: str


class Plan(PlanDraft):
    """A saved plan version: the draft plus everything the server computed."""

    model_config = ConfigDict(extra="forbid")
    project: str
    version: int
    org: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    based_on_version: int | None = None
    delivery: dict[str, Delivery] = Field(default_factory=dict)
    coverage: list[CoverageEntry] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
