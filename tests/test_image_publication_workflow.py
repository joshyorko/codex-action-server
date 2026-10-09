import ast
import os
from pathlib import Path
import re
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def workflow():
    return yaml.load(
        (ROOT / ".github/workflows/container.yml").read_text(), Loader=yaml.BaseLoader
    )


def allowed(
    expression, event, ref, publish=False, repository="joshyorko/codex-action-server"
):
    values = {
        "github.event_name": event,
        "github.ref": ref,
        "github.repository": repository,
        "inputs.publish": publish,
    }
    expression = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    for key, value in values.items():
        expression = expression.replace(key, repr(value))
    expression = expression.replace("&&", " and ").replace("||", " or ")
    expression = re.sub(r"!(?!=)", " not ", expression).strip()
    tree = ast.parse(expression, mode="eval")
    accepted = (
        ast.Expression,
        ast.BoolOp,
        ast.UnaryOp,
        ast.Compare,
        ast.Constant,
        ast.And,
        ast.Or,
        ast.Not,
        ast.Eq,
        ast.Call,
        ast.Name,
        ast.Load,
    )
    assert all(isinstance(node, accepted) for node in ast.walk(tree))
    assert all(
        node.id == "startsWith" for node in ast.walk(tree) if isinstance(node, ast.Name)
    )
    return bool(
        eval(
            compile(tree, "workflow-condition", "eval"),
            {
                "__builtins__": {},
                "startsWith": lambda value, prefix: value.startswith(prefix),
            },
        )
    )


@pytest.mark.parametrize(
    "event,ref,choice,expected",
    [
        ("pull_request", "refs/pull/13/merge", True, False),
        ("pull_request", "refs/heads/main", True, False),
        ("push", "refs/heads/fix/runtime", False, False),
        ("push", "refs/heads/main", False, True),
        ("push", "refs/heads/feat/container-control-plane", False, True),
        ("push", "refs/tags/v0.2.1", False, True),
        ("push", "refs/tags/not-a-release", False, False),
        ("workflow_dispatch", "refs/heads/fix/runtime", False, False),
        ("workflow_dispatch", "refs/heads/fix/runtime", True, True),
        ("schedule", "refs/heads/main", True, False),
    ],
)
def test_real_workflow_routes_verification_and_publication(
    event, ref, choice, expected
):
    jobs = workflow()["jobs"]
    assert allowed(jobs["publish"]["if"], event, ref, choice) is expected
    assert allowed(jobs["verify"]["if"], event, ref, choice) is (not expected)
    assert not allowed(jobs["publish"]["if"], event, ref, choice, "other/fork")
    assert allowed(jobs["verify"]["if"], event, ref, choice, "other/fork")


def test_publication_uses_the_verified_local_image_without_artifact_handoff():
    jobs = workflow()["jobs"]
    assert jobs["verify"]["permissions"]["packages"] == "read"
    assert jobs["publish"]["permissions"]["packages"] == "write"
    assert (
        workflow()["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] == "false"
    )
    for job in jobs.values():
        for step in job["steps"]:
            assert "upload-artifact" not in step.get("uses", "")
            assert "download-artifact" not in step.get("uses", "")
            assert "docker save" not in step.get("run", "")
            if "run" in step:
                result = subprocess.run(
                    ["bash", "-n"], input=step["run"], text=True, capture_output=True
                )
                assert result.returncode == 0, result.stderr
    names = [step.get("name", "") for step in jobs["publish"]["steps"]]
    assert names.index("Verify the exact local image") < names.index(
        "Authenticate for GHCR publication"
    )
    assert names.index("Authenticate for GHCR publication") < names.index(
        "Publish immutable commit and release tags"
    )


def test_release_reuses_the_commit_image_without_rebuilding():
    jobs = workflow()["jobs"]
    assert all(
        "GHCR_TOKEN" not in step.get("env", {}) for step in jobs["verify"]["steps"]
    )
    steps = jobs["publish"]["steps"]
    release = next(step for step in steps if step.get("id") == "release_image")
    build = next(
        step for step in steps if step.get("name") == "Build the exact source image"
    )
    assert "startsWith(github.ref, 'refs/tags/v')" in release["if"]
    assert 'docker pull "$image"' in release["run"]
    assert 'test "$revision" = "$GITHUB_SHA"' in release["run"]
    assert build["if"] == "steps.release_image.outcome != 'success'"
    assert steps.index(release) < steps.index(build)


def test_only_publication_has_registry_write_permission():
    definition = workflow()
    assert definition["permissions"] == {"contents": "read"}
    assert definition["on"]["push"]["branches"] == [
        "main",
        "feat/container-control-plane",
    ]
    assert definition["on"]["push"]["tags"] == ["v*"]
    assert definition["jobs"]["verify"]["permissions"] == {
        "contents": "read",
        "packages": "read",
    }
    assert definition["jobs"]["publish"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    for step in definition["jobs"]["publish"]["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] == "false"


@pytest.mark.parametrize(
    "state,ref,current_head,success,pushes",
    [
        ("same", "refs/heads/main", "abc123", True, 1),
        ("different", "refs/heads/main", "abc123", False, 0),
        ("missing", "refs/heads/main", "abc123", True, 2),
        ("missing", "refs/heads/main", "newer", True, 1),
        ("missing", "refs/heads/feat/container-control-plane", "abc123", True, 1),
        ("missing", "refs/tags/v0.2.1", "abc123", True, 2),
        ("denied", "refs/heads/main", "abc123", False, 0),
    ],
)
def test_actual_publication_script_preserves_immutable_tags(
    tmp_path, monkeypatch, state, ref, current_head, success, pushes
):
    executable = tmp_path / "docker"
    executable.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\\n' "$*" >> "$MOCK_CALLS"
case "$1 $2" in
  "image inspect") printf 'sha256:verified\\n' ;;
  "manifest inspect")
    case "$MOCK_STATE" in
      same) printf '{"config":{"digest":"sha256:verified"}}\\n' ;;
      different) printf '{"config":{"digest":"sha256:other"}}\\n' ;;
      missing) printf 'manifest unknown\\n' >&2; exit 1 ;;
      denied) printf 'unauthorized: authentication required\\n' >&2; exit 1 ;;
      *) exit 2 ;;
    esac ;;
  "tag codex-action-server:verified") exit 0 ;;
  "push ghcr.io/joshyorko/codex-action-server:"*) exit 0 ;;
  "buildx imagetools") printf 'sha256:manifest\\n' ;;
  *) printf 'Unexpected docker invocation\\n' >&2; exit 2 ;;
esac
"""
    )
    executable.chmod(0o755)
    git = tmp_path / "git"
    git.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\trefs/heads/main\\n" "$MOCK_MAIN_SHA"\n'
    )
    git.chmod(0o755)
    monkeypatch.setenv("MOCK_MAIN_SHA", current_head)
    calls = tmp_path / "calls"
    summary = tmp_path / "summary"
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setenv("MOCK_CALLS", str(calls))
    monkeypatch.setenv("MOCK_STATE", state)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("GITHUB_REF", ref)
    script = next(
        step["run"]
        for step in workflow()["jobs"]["publish"]["steps"]
        if step.get("name") == "Publish immutable commit and release tags"
    )
    result = subprocess.run(["bash", "-c", script], text=True, capture_output=True)
    assert (result.returncode == 0) is success, result.stderr
    recorded = calls.read_text().splitlines()
    assert sum(line.startswith("push ") for line in recorded) == pushes
    assert all(
        "codex-action-server:verified" in line
        for line in recorded
        if line.startswith("tag ")
    )
    assert ("push ghcr.io/joshyorko/codex-action-server:latest" in recorded) is (
        success and ref == "refs/heads/main" and current_head == "abc123"
    )
    assert not any(line.startswith("build ") for line in recorded)
    assert summary.exists() is success
