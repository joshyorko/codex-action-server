import ast
import json
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


def qualification_workflow():
    return yaml.load(
        (ROOT / ".github/workflows/codex-version-qualification.yml").read_text(),
        Loader=yaml.BaseLoader,
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


def test_publication_uses_the_verified_image_and_emits_an_immutable_handoff():
    jobs = workflow()["jobs"]
    assert jobs["verify"]["permissions"]["packages"] == "read"
    assert jobs["publish"]["permissions"]["packages"] == "write"
    assert jobs["publish"]["permissions"]["actions"] == "read"
    assert (
        workflow()["on"]["workflow_dispatch"]["inputs"]["publish"]["default"] == "false"
    )
    for job in jobs.values():
        for step in job["steps"]:
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
    handoff = next(
        step for step in jobs["publish"]["steps"] if step.get("id") == "handoff"
    )
    upload = next(
        step
        for step in jobs["publish"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    guard = next(
        step for step in jobs["publish"]["steps"] if step.get("id") == "handoff_guard"
    )
    assert handoff["if"] == "github.ref == 'refs/heads/main'"
    assert guard["if"] == "steps.handoff.outputs.status == 'created'"
    assert "git/ref/heads/main" in guard["run"]
    assert upload["if"] == "steps.handoff_guard.outputs.status == 'created'"
    assert (
        upload["with"]["name"]
        == "cas-qualified-release-${{ github.sha }}-${{ github.run_id }}"
    )
    assert upload["with"]["path"] == "${{ runner.temp }}/qualified-release.json"
    assert upload["with"]["retention-days"] == "90"
    assert "--qualification-run-url" in handoff["run"]
    assert "actions/workflows/$workflow/runs" in handoff["run"]


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
        "actions": "read",
    }
    for step in definition["jobs"]["publish"]["steps"]:
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] == "false"


def _run_handoff_step(tmp_path, *, main_sha):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"--jq .object.sha"* ]]; then
  printf '%s\\n' "$MOCK_MAIN_SHA"
  exit 0
fi
case "$2" in
  */codex-version-qualification.yml/runs*) run_id=123 ;;
  */test.yml/runs*) run_id=124 ;;
  *) echo "unexpected gh api request" >&2; exit 2 ;;
esac
printf '{"workflow_runs":[{"head_sha":"%s","head_branch":"main","event":"push","status":"completed","conclusion":"success","html_url":"https://github.com/joshyorko/codex-action-server/actions/runs/%s","run_number":1,"run_attempt":1}]}\\n' "$GITHUB_SHA" "$run_id"
"""
    )
    gh.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/usr/bin/env bash\nexit 0\n")
    sleep.chmod(0o755)
    temp = tmp_path / "runner-temp"
    temp.mkdir()
    output = tmp_path / "github-output"
    summary = tmp_path / "step-summary"
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    env = {
        **os.environ,
        "PATH": str(bin_dir) + ":" + os.environ["PATH"],
        "GITHUB_REPOSITORY": "joshyorko/codex-action-server",
        "GITHUB_SHA": commit,
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_RUN_ID": "125",
        "GH_TOKEN": "test-token",
        "IMAGE_DIGEST": "sha256:" + "f" * 64,
        "IMAGE_REVISION": commit,
        "MOCK_MAIN_SHA": main_sha,
        "RUNNER_TEMP": str(temp),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(summary),
    }
    script = next(
        step["run"]
        for step in workflow()["jobs"]["publish"]["steps"]
        if step.get("id") == "handoff"
    )
    result = subprocess.run(
        ["bash", "-c", script], env=env, text=True, capture_output=True
    )
    return result, output, summary, temp


def test_handoff_skips_if_main_has_advanced(tmp_path):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result, output, summary, temp = _run_handoff_step(
        tmp_path, main_sha="f" * 40 if commit != "f" * 40 else "e" * 40
    )
    assert result.returncode == 0, result.stderr
    assert "status=skipped_main_advanced" in output.read_text()
    assert "no receipt emitted" in summary.read_text()
    assert not (temp / "qualified-release.json").exists()


def test_handoff_does_not_emit_artifact_for_incomplete_merged_surface(tmp_path):
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    result, output, summary, temp = _run_handoff_step(tmp_path, main_sha=commit)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "skipped_incomplete_qualification"
    assert "status=skipped_incomplete_qualification" in output.read_text()
    assert "no receipt emitted" in summary.read_text()
    assert not (temp / "qualified-release.json").exists()


def test_codex_qualification_is_scheduled_serial_and_fail_closed():
    definition = qualification_workflow()
    assert definition["on"]["schedule"] == [{"cron": "17 6 * * *"}]
    assert definition["on"]["push"]["branches"] == ["main"]
    assert ".github/schemas/**" in definition["on"]["push"]["paths"]
    assert ".github/schemas/**" in definition["on"]["pull_request"]["paths"]
    assert "tests/fixtures/protocol/**" in definition["on"]["push"]["paths"]
    assert "ci/codex_qualification/**" in definition["on"]["push"]["paths"]
    assert definition["concurrency"]["cancel-in-progress"] == "false"
    assert definition["jobs"]["generate-candidate-schema"]["permissions"] == {
        "contents": "read"
    }
    open_pr = definition["jobs"]["open-qualified-pr"]
    assert "proposal_status == 'prepared'" in open_pr["if"]
    branch_writer = next(
        step
        for step in open_pr["steps"]
        if step.get("name") == "Apply candidate to a dedicated branch and verify"
    )
    assert (
        "Refusing to replace a qualification branch with human-authored commits"
        in branch_writer["run"]
    )
    assert 'git push origin "$branch"' in branch_writer["run"]
    assert "cancel-in-progress" not in open_pr


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
  "image inspect")
    if [[ "$*" == *org.opencontainers.image.revision* ]]; then
      printf '%s\\n' "$GITHUB_SHA"
    else
      printf 'sha256:verified\\n'
    fi ;;
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
  "buildx imagetools") printf 'sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\\n' ;;
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
    output = tmp_path / "output"
    monkeypatch.setenv("PATH", str(tmp_path) + ":" + os.environ["PATH"])
    monkeypatch.setenv("MOCK_CALLS", str(calls))
    monkeypatch.setenv("MOCK_STATE", state)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
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
