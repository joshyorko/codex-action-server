import json
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "codex_qualification", ROOT / "ci/qualify_codex_schema.py"
)
qualification = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qualification)


def test_schema_normalization_ignores_only_documentation_noise():
    baseline = {
        "$schema": "draft-07",
        "title": "ServerDiagnosticsResponse",
        "description": "before",
        "type": "object",
        "properties": {"value": {"type": ["integer", "null"]}},
    }
    candidate = {
        "title": "Generated response",
        "description": "after",
        "type": "object",
        "properties": {"value": {"type": ["integer", "null"]}},
    }
    assert qualification.normalize(baseline) == qualification.normalize(candidate)


def test_schema_normalization_preserves_contract_changes():
    baseline = {"type": ["integer", "null"]}
    changed = {"type": "integer"}
    assert qualification.normalize(baseline) != qualification.normalize(changed)


def test_0166_diagnostics_fixture_matches_reviewed_contract():
    candidate = json.loads(
        (
            ROOT / "tests/fixtures/protocol/codex_0.166.0_server_diagnostics.json"
        ).read_text()
    )
    baseline = json.loads(
        (
            ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
        ).read_text()
    )
    assert candidate["native_version"] == "0.166.0"
    assert candidate["method"] == "server/diagnostics"
    assert qualification.normalize(candidate["schemas"]) == qualification.normalize(
        baseline["schemas"]
    )
