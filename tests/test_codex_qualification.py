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
