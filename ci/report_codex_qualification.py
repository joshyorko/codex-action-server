#!/usr/bin/env python3
"""Build a sanitized issue report for an unqualified native candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_STATUSES = {
    "unqualified",
    "unqualified_regression_tests_not_run",
    "unqualified_live_protocol_smoke",
    "needs_review",
    "failed",
}


def _load(directory: Path, name: str):
    path = directory / name
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _digest(value) -> str:
    return value if isinstance(value, str) and _SHA256.fullmatch(value) else "unavailable"


def build_report(artifact: Path, version_hint: str, run_url: str, workflow_result: str):
    provenance = _load(artifact, "provenance.json") or {}
    diff = _load(artifact, "server-diagnostics-schema-diff.json") or {}
    smoke = _load(artifact, "protocol-smoke.json") or {}
    version = provenance.get("version") or version_hint
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        version = "unresolved"
    status = provenance.get("qualification")
    if status not in _ALLOWED_STATUSES:
        status = "failed" if workflow_result != "success" else "unqualified"
    identical = diff.get("identical")
    if not isinstance(identical, bool):
        identical = "not available"
    smoke_status = smoke.get("status")
    if smoke_status not in {"passed", "failed", "invalid_response", "not_run"}:
        smoke_status = provenance.get("live_protocol_smoke", "not_run")
    if smoke_status not in {"passed", "failed", "invalid_response", "not_run"}:
        smoke_status = "not_run"
    typed_status = provenance.get("typed_regression_tests", "not_run")
    if typed_status not in {"passed", "not_run"}:
        typed_status = "not_run"
    if workflow_result not in {"success", "failure", "cancelled", "skipped"}:
        workflow_result = "unknown"
    run_url = (
        run_url
        if run_url.startswith("https://github.com/")
        and not any(char.isspace() for char in run_url)
        else "unavailable"
    )
    source_tag = provenance.get("source_tag")
    if source_tag != f"rust-v{version}":
        source_tag = "unavailable"
    expected_asset_url = (
        f"https://github.com/openai/codex/releases/download/{source_tag}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    asset_url = (
        expected_asset_url
        if provenance.get("source_url") == expected_asset_url
        and source_tag != "unavailable"
        else "unavailable"
    )
    title = f"Codex diagnostics qualification needs review: {version}"
    body = "\n".join(
        (
            f"Codex `{version}` was not admitted for `{provenance.get('method', 'server/diagnostics')}`.",
            "",
            "## Qualification evidence",
            f"- Workflow verdict: `{status}` (workflow result: `{workflow_result}`).",
            f"- Stable source tag: `{source_tag}`.",
            f"- Source asset: `{asset_url}`.",
            f"- Asset SHA-256: `{_digest(provenance.get('source_asset_sha256'))}`.",
            f"- Binary SHA-256: `{_digest(provenance.get('binary_sha256'))}`.",
            f"- Diagnostics schema identical to reviewed baseline: `{identical}`.",
            f"- Typed regressions: `{typed_status}`.",
            f"- Live `server/diagnostics` protocol smoke: `{smoke_status}`.",
            f"- Workflow run and attached bounded diff/provenance artifacts: {run_url}",
            "",
            "## Maintainer action",
            "Review the generated method-level schema diff and live response-shape evidence. Keep the exact-version/method gate closed until both the schema and disposable protocol smoke are reviewed. Do not use a semver range or a runtime banner as approval evidence.",
        )
    )
    return title, body


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--candidate-version", default="")
    parser.add_argument("--workflow-result", default="failure")
    args = parser.parse_args()
    title, body = build_report(
        args.artifact,
        args.candidate_version,
        args.run_url,
        args.workflow_result,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "issue-title.txt").write_text(title + "\n")
    (args.output / "issue-body.md").write_text(body + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
