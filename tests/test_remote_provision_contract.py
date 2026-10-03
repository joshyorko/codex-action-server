"""Ownership and safety of the native Devsy workflow, not live provisioning proof."""

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_recipe_owner_documents_native_lifecycle_and_distinct_gates():
    text = " ".join((ROOT / "docs/REMOTE_WORKERS.md").read_text().split())
    for value in [
        "provider_list",
        "workspace_list",
        "workspace_status",
        "workspace_create",
        "workspace_start",
        "workspace_exec",
        "Multiple matches",
        "Do not blindly replay",
        "Never use",
        "TTY authentication",
        "Operator auth",
        "canary",
        "not live Kubernetes",
        "one central",
        ".devcontainer/remote-worker/devcontainer.json",
    ]:
        assert value.lower() in text.lower()
    assert "Podman/Docker" in text


def test_source_example_does_not_pin_recreated_identity():
    import json

    target = json.loads((ROOT / "config/targets.example.json").read_text())["targets"][
        "devsy"
    ]
    assert target["source"] == "https://github.com/joshyorko/codex-action-server.git"
    assert "workspace" not in target
    assert "workspace_uid" not in target


def test_recipe_never_starts_a_central_server():
    text = "\n".join(p.read_text() for p in (ROOT / "scripts/remote").glob("*.sh"))
    definition = (ROOT / ".devcontainer/remote-worker/devcontainer.json").read_text()
    assert "app-server daemon start" in definition
    for forbidden in ["action-server start", "tunnel-client", "hermes", "kubectl"]:
        assert forbidden not in text + definition
