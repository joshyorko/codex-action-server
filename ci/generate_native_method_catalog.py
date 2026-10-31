#!/usr/bin/env python3
"""Validate the reviewed catalog against CAS's native RPC and action surfaces."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ci/codex_qualification/native_methods.json"
RPC_SOURCE = ROOT / "src/codex_rpc.py"
CAPABILITY_SOURCE = ROOT / "src/native_capabilities.py"
CONTRACT_SOURCE = ROOT / "src/action_catalog_contract.py"
EXPERIMENTAL_BASELINE = (
    ROOT
    / "tests/fixtures/protocol/codex_0.160.1_to_0.161.0_experimental_compatibility.json"
)
DIAGNOSTICS_BASELINE = (
    ROOT / "tests/fixtures/protocol/codex_0.162.1_server_diagnostics.json"
)

READ_ONLY_PROTOCOL_SMOKE = {
    "account/rateLimits/read",
    "app/list",
    "hooks/list",
    "mcpServerStatus/list",
    "model/list",
    "modelProvider/capabilities/read",
    "plugin/list",
    "server/diagnostics",
    "skills/list",
    "thread/list",
    "thread/loaded/list",
    "threadSection/list",
}

# The protocol's Rust type aliases are not uniformly derived from method names.
# Keep these exact generated schema IDs explicit and test them against the pinned
# upstream candidate during qualification.
SCHEMA_TYPE_OVERRIDES = {
    "account/rateLimits/read": (
        "Nullable_GetAccountRateLimitsParams",
        "GetAccountRateLimitsResponse",
    ),
    "account/usage/read": (
        "Nullable_GetAccountTokenUsageParams",
        "GetAccountTokenUsageResponse",
    ),
    "app/list": ("AppsListParams", "AppsListResponse"),
    "mcpServer/resource/read": ("McpResourceReadParams", "McpResourceReadResponse"),
    "mcpServerStatus/list": (
        "ListMcpServerStatusParams",
        "ListMcpServerStatusResponse",
    ),
    "thread/name/set": ("ThreadSetNameParams", "ThreadSetNameResponse"),
}


def _assigned_literal(source: Path, name: str) -> set[str]:
    tree = ast.parse(source.read_text())
    for node in tree.body:
        targets = (
            node.targets
            if isinstance(node, ast.Assign)
            else [node.target]
            if isinstance(node, ast.AnnAssign)
            else []
        )
        if not any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            continue
        value = node.value
        if (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Name)
            and value.func.id == "frozenset"
        ):
            value = value.args[0]
        result = ast.literal_eval(value)
        if not isinstance(result, (set, frozenset)) or not all(
            isinstance(item, str) for item in result
        ):
            raise ValueError(f"{name} must be a literal set of strings")
        return set(result)
    raise ValueError(f"could not find {name} in {source.relative_to(ROOT)}")


def _pascal_method(method: str) -> str:
    words = re.findall(
        r"[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)|[0-9]+",
        method.replace("/", "_").replace("-", "_"),
    )
    return "".join(word[:1].upper() + word[1:] for word in words)


def _method_types_from_fixtures() -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    compatibility = json.loads(EXPERIMENTAL_BASELINE.read_text())
    for item in compatibility.get("cas_experimental_contracts", []):
        result[item["method"]] = (item["request_type"], item["response_type"])
    diagnostics = json.loads(DIAGNOSTICS_BASELINE.read_text())
    result[diagnostics["method"]] = (
        "ServerDiagnosticsParams",
        "ServerDiagnosticsResponse",
    )
    return result


def _action_capabilities_by_method() -> dict[str, list[dict]]:
    sys.path.insert(0, str(ROOT / "src"))
    from action_catalog_contract import CAPABILITIES

    result: dict[str, list[dict]] = {}
    for action_name, capability in CAPABILITIES.items():
        contract = {
            "action": action_name,
            "group": capability.group,
            "packages": sorted(capability.packages),
            "profiles": sorted(capability.profiles),
            "is_consequential": capability.is_consequential,
            "read_only_hint": capability.read_only_hint,
            "destructive_hint": capability.destructive_hint,
            "idempotent_hint": capability.idempotent_hint,
            "open_world_hint": capability.open_world_hint,
        }
        for method in capability.native_methods:
            result.setdefault(method, []).append(contract)
    return {
        method: sorted(contracts, key=lambda contract: contract["action"])
        for method, contracts in result.items()
    }


def _regression_files(method: str, owners: list[str]) -> list[str]:
    tests = sorted((ROOT / "tests").glob("test_*.py"))
    needles = {method, *owners}
    matched = [
        path.relative_to(ROOT).as_posix()
        for path in tests
        if any(needle in path.read_text(errors="ignore") for needle in needles)
    ]
    required = ["tests/test_protocol_contracts.py"]
    return sorted(set(required + matched))


def expected_catalog() -> dict:
    methods = _assigned_literal(RPC_SOURCE, "METHODS")
    experimental = _assigned_literal(RPC_SOURCE, "EXPERIMENTAL_METHODS")
    observe = _assigned_literal(CAPABILITY_SOURCE, "OBSERVE_METHODS")
    action_capabilities = _action_capabilities_by_method()
    fixture_types = _method_types_from_fixtures()

    if not experimental <= methods:
        raise ValueError(
            "experimental methods are outside the typed native RPC surface"
        )
    missing_owners = methods - action_capabilities.keys()
    if missing_owners:
        raise ValueError(
            "native methods lack typed action owners: "
            + ", ".join(sorted(missing_owners))
        )

    rows = []
    for method in sorted(methods):
        request_type, response_type = SCHEMA_TYPE_OVERRIDES.get(
            method,
            fixture_types.get(
                method,
                (
                    f"{_pascal_method(method)}Params",
                    f"{_pascal_method(method)}Response",
                ),
            ),
        )
        owner_capabilities = action_capabilities[method]
        method_owners = [item["action"] for item in owner_capabilities]
        read_only = method in observe
        if method in READ_ONLY_PROTOCOL_SMOKE:
            smoke_strategy = "disposable_protocol_smoke"
        elif read_only:
            smoke_strategy = "typed_read_fixture"
        else:
            smoke_strategy = "disposable_mutation_fixture"
        rows.append(
            {
                "method": method,
                "request_schema": request_type,
                "response_schema": response_type,
                "classification": "experimental"
                if method in experimental
                else "stable",
                "required_capabilities": ["experimentalApi"]
                if method in experimental
                else [],
                "access": "read_only" if read_only else "mutating_or_external",
                "smoke_strategy": smoke_strategy,
                "owning_typed_actions": method_owners,
                "action_capabilities": owner_capabilities,
                "regression_tests": _regression_files(method, method_owners),
            }
        )
    return {"schema_version": 1, "methods": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if args.check == args.write:
        parser.error("choose exactly one of --check or --write")
    generated = json.dumps(expected_catalog(), indent=2, sort_keys=True) + "\n"
    if args.write:
        CATALOG.parent.mkdir(parents=True, exist_ok=True)
        CATALOG.write_text(generated)
        return 0
    if not CATALOG.is_file() or CATALOG.read_text() != generated:
        print(
            f"{CATALOG.relative_to(ROOT)} is stale; review and regenerate it",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
