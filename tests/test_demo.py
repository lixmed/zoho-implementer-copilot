import os

import keyring

from copilot import cli


def test_demo_runs_offline_and_restores_environment(tmp_path, capsys, isolated):
    before_home = os.environ.get("COPILOT_HOME")
    assert cli.main(["demo", "--out", str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    assert "validation catches a made-up field" in out and "Leads.Lead_Score" in out
    assert "applied 6 operations" in out and "soft rollback" in out
    assert "Change request: plan v1" in out
    project = tmp_path / "demo" / "projects" / "acme-realty" / "onboarding"
    for rel in ["plans/plan.v1.md", "plans/plan.v2.md", "exports/signoff-v1.md", "exports/uat-v1.md",
                "exports/change-request-v1-to-v2.md", "handoff/functions/post_hot_lead.dg",
                "handoff/connections.md"]:  # fmt: skip
        assert (project / rel).exists(), rel
    assert any((project / "changesets").iterdir())
    # the demo used its own in-memory keychain and folders, then put everything back
    assert keyring.get_keyring() is isolated
    assert isolated.store == {}
    assert os.environ.get("COPILOT_HOME") == before_home


def test_doctor_and_version(capsys):
    assert cli.main(["doctor"]) == 1  # nothing configured in the isolated test env
    out = capsys.readouterr().out
    assert "zoho api client" in out and "copilot demo" in out
    try:
        cli.main(["--version"])
    except SystemExit as exc:
        assert exc.code == 0
    assert "copilot 0." in capsys.readouterr().out
