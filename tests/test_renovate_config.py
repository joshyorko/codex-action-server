"""CAS Renovate enrollment inherits Patchraptor with review-only updates."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_local_renovate_config_inherits_patchraptor_and_requires_review():
    config = json.loads((ROOT / ".github/renovate.json5").read_text())

    assert config["extends"] == [
        "github>joshyorko/renovate-config:org-inherited-config"
    ]
    assert config["enabledManagers"] == [
        "pep621",
        "pip_requirements",
        "github-actions",
        "dockerfile",
    ]
    assert config["automerge"] is False
    assert config["platformAutomerge"] is False
    assert config["dependencyDashboardApproval"] is True
    assert config["prCreation"] == "status-success"
    assert config["prConcurrentLimit"] == 1
    assert config["prHourlyLimit"] == 1

    protected_rule = config["packageRules"][0]
    assert {"pin", "pinDigest", "digest"} <= set(protected_rule["matchUpdateTypes"])
    assert protected_rule["automerge"] is False
    assert protected_rule["platformAutomerge"] is False
    assert protected_rule["dependencyDashboardApproval"] is True
    assert protected_rule["prCreation"] == "status-success"
