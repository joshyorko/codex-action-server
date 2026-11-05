#!/usr/bin/env python3
"""Build a bounded blocker issue for native methods withheld from admission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

_VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_METHOD_STATUS = {"compatible_pending_review", "needs_review", "unqualified"}
_REASON = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _load(directory: Path, name: str):
    path = directory / name
    if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _digest(value) -> str:
    return (
        value if isinstance(value, str) and _SHA256.fullmatch(value) else "unavailable"
    )


def build_report(artifact: Path, version_hint: str, run_url: str, workflow_result: str):
    provenance = _load(artifact, "provenance.json") or {}
    report = _load(artifact, "native-methods.json") or {}
    version = report.get("version") or provenance.get("version") or version_hint
    if not isinstance(version, str) or _VERSION.fullmatch(version) is None:
        version = "unresolved"
    if workflow_result not in {"success", "failure", "cancelled", "skipped"}:
        workflow_result = "unknown"
    run_url = (
        run_url
        if run_url.startswith("https://github.com/")
        and not any(char.isspace() for char in run_url)
        else "unavailable"
    )
    expected_tag = f"rust-v{version}"
    source_tag = report.get("source_tag")
    if source_tag != expected_tag:
        source_tag = "unavailable"
    expected_asset_url = (
        f"https://github.com/openai/codex/releases/download/{source_tag}/"
        "codex-x86_64-unknown-linux-musl.tar.gz"
    )
    source_url = report.get("source_url")
    if source_url != expected_asset_url or source_tag == "unavailable":
        source_url = "unavailable"

    rows = report.get("methods", [])
    if not isinstance(rows, list):
        rows = []
    withheld = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or row.get("status") == "compatible_pending_review"
        ):
            continue
        method = row.get("method")
        if not isinstance(method, str) or not re.fullmatch(
            r"[A-Za-z0-9/_-]{1,128}", method
        ):
            method = "unknown_method"
        status = (
            row.get("status") if row.get("status") in _METHOD_STATUS else "unqualified"
        )
        reason = row.get("reason_code")
        if not isinstance(reason, str) or _REASON.fullmatch(reason) is None:
            reason = "qualification_evidence_unavailable"
        typed = row.get("typed_regressions")
        if typed not in {"passed", "not_run", "invalid_evidence"}:
            typed = "not_run"
        smoke = row.get("smoke", {})
        smoke_status = smoke.get("status") if isinstance(smoke, dict) else None
        if not isinstance(smoke_status, str) or _REASON.fullmatch(smoke_status) is None:
            smoke_status = "not_run"
        withheld.append((method, status, reason, typed, smoke_status))
    withheld.sort()

    overall_status = report.get("qualification")
    if not isinstance(overall_status, str) or _REASON.fullmatch(overall_status) is None:
        overall_status = "failed" if workflow_result != "success" else "unqualified"
    counts = report.get("counts", {})
    if not isinstance(counts, dict):
        counts = {}
    lines = [
        f"Codex `{version}` has native methods withheld from exact-version qualification.",
        "",
        "## Qualification evidence",
        f"- Workflow verdict: `{overall_status}` (workflow result: `{workflow_result}`).",
        f"- Stable source tag: `{source_tag}`.",
        f"- Source asset: `{source_url}`.",
        f"- Asset SHA-256: `{_digest(report.get('source_asset_sha256'))}`.",
        f"- Binary SHA-256: `{_digest(report.get('binary_sha256'))}`.",
        f"- Catalog methods: `{counts.get('total', len(rows))}`; compatible pending review: `{counts.get('compatible_pending_review', 0)}`; withheld: `{len(withheld)}`.",
        f"- Workflow run and bounded evidence artifact: {run_url}",
        "",
        "## Withheld methods",
    ]
    lines.extend(
        f"- `{method}`: status `{status}`, reason `{reason}`, typed regressions `{typed}`, smoke `{smoke}`."
        for method, status, reason, typed, smoke in withheld
    )
    if not withheld:
        lines.append(
            "- No method-level details were available; inspect the bounded artifact."
        )
    lines.extend(
        (
            "",
            "## Maintainer action",
            "Review the method-specific schema fingerprints and typed regression evidence. Keep each withheld experimental method outside the exact-version approval table until its own evidence passes review. Do not infer daemon installation or deployment state from this workflow.",
        )
    )
    title = f"Codex native method qualification needs review: {version}"
    return title, "\n".join(lines)


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
