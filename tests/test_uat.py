from copilot.planner.schema import CoverageEntry, Plan
from copilot.planner.uat import render_uat_script
from copilot.projects.requirements import Unit

UNITS = [
    Unit(id="k:1", doc="k", text="Agents must see the property on a deal."),
    Unit(id="k:2", doc="k", text="Alert agents about hot leads."),
    Unit(id="k:3", doc="k", text="Nightly listing import."),
    Unit(id="k:4", doc="k", text="Hello team."),
]


def plan(coverage):
    return Plan.model_validate(
        {
            "summary": "s",
            "project": "acme/p1",
            "version": 3,
            "items": [
                {
                    "kind": "layout",
                    "id": "P-001",
                    "title": "Property on deals",
                    "rationale": "r",
                    "sources": ["k:1"],
                    "estimate_minutes": 5,
                    "module": "Deals",
                    "layout": "Standard",
                    "create_new": False,
                    "changes": ["add Property"],
                    "acceptance_test": "Open a deal and see Property",
                },
                {
                    "kind": "field",
                    "id": "P-002",
                    "title": "Lead temperature",
                    "rationale": "r",
                    "sources": ["k:2"],
                    "estimate_minutes": 5,
                    "module": "Leads",
                    "field_label": "Lead Temperature",
                    "api_name": "Lead_Temperature",
                    "data_type": "picklist",
                    "picklist_values": ["Hot"],
                    "acceptance_test": "Set a lead to Hot",
                },
                {
                    "kind": "workflow",
                    "id": "P-003",
                    "title": "Hot lead email",
                    "rationale": "r",
                    "sources": ["k:2"],
                    "estimate_minutes": 5,
                    "module": "Leads",
                    "trigger": "edit",
                    "criteria": "Hot",
                    "actions": [{"type": "email_notification", "details": "x"}],
                },
            ],
            "tickets": [
                {
                    "id": "T-001",
                    "title": "Import",
                    "description": "d",
                    "acceptance_criteria": ["a"],
                    "estimate_hours": 1,
                    "sources": ["k:3"],
                }
            ],
            "coverage": [c.model_dump() for c in coverage],
        }  # fmt: skip
    )


def test_full_script():
    p = plan(
        [
            CoverageEntry(unit_id="k:1", status="covered", refs=["P-001"]),
            CoverageEntry(unit_id="k:2", status="covered", refs=["P-003", "P-002", "Q-001"]),
            CoverageEntry(unit_id="k:3", status="covered", refs=["T-001"]),
            CoverageEntry(unit_id="k:4", status="out_of_scope", note="greeting"),
        ]
    )
    assert render_uat_script(p, UNITS, "Acme Realty") == (
        "# UAT script: Acme Realty (plan v3)\n"
        "\n"
        "## k:1: Agents must see the property on a deal.\n"
        "\n"
        "- [ ] P-001 Property on deals: Open a deal and see Property\n"
        "\n"
        "## k:2: Alert agents about hot leads.\n"
        "\n"
        "- [ ] P-003 Hot lead email: (no acceptance test, ask the implementer)\n"
        "- [ ] P-002 Lead temperature: Set a lead to Hot\n"
    )


def test_nothing_to_test():
    p = plan([CoverageEntry(unit_id="k:4", status="out_of_scope", note="greeting")])
    assert render_uat_script(p, UNITS, "Acme") == (
        "# UAT script: Acme (plan v3)\n\n_Nothing to test yet._\n"
    )
