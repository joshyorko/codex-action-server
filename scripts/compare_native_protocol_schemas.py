#!/usr/bin/env python3
"""Compare CAS experimental RPC contracts between generated Codex schemas."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
from typing import Any

UNORDERED_SCHEMA_ARRAYS = {"allOf", "anyOf", "enum", "oneOf", "required", "type"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cas_methods(source_path: Path) -> tuple[set[str], set[str]]:
    tree = ast.parse(source_path.read_text())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [
                target.id for target in node.targets if isinstance(target, ast.Name)
            ]
            if any(name in {"METHODS", "EXPERIMENTAL_METHODS"} for name in names):
                for name in names:
                    if name in {"METHODS", "EXPERIMENTAL_METHODS"}:
                        values[name] = ast.literal_eval(node.value)
    return set(values["METHODS"]), set(values["EXPERIMENTAL_METHODS"])


def load_definitions(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())["definitions"]


def expand_schema(value: Any, definitions: dict[str, Any], stack=()) -> Any:
    if isinstance(value, list):
        return [expand_schema(item, definitions, stack) for item in value]
    if not isinstance(value, dict):
        return value

    reference = value.get("$ref")
    if reference and reference.startswith("#/definitions/"):
        name = reference.removeprefix("#/definitions/")
        if name in definitions and name not in stack:
            return expand_schema(definitions[name], definitions, (*stack, name))
        return {"$ref": reference}

    return {
        key: expand_schema(item, definitions, stack)
        for key, item in value.items()
        if key
        not in {
            "$schema",
            "$comment",
            "default",
            "description",
            "examples",
            "title",
        }
    }


def canonical(value: Any, parent_key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {key: canonical(item, key) for key, item in sorted(value.items())}
    if isinstance(value, list):
        items = [canonical(item) for item in value]
        if parent_key in UNORDERED_SCHEMA_ARRAYS:
            return sorted(items, key=lambda item: json.dumps(item, sort_keys=True))
        return items
    return value


def contract_hash(value: Any) -> str:
    encoded = json.dumps(canonical(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def schema_methods(path: Path) -> set[str]:
    client = json.loads(path.read_text())["definitions"]["ClientRequest"]
    return {variant["properties"]["method"]["enum"][0] for variant in client["oneOf"]}


def build_comparison(args: argparse.Namespace) -> dict[str, Any]:
    cas_methods, experimental_methods = load_cas_methods(args.rpc_source)
    old_contracts = json.loads(args.old_contracts.read_text())["contracts"]
    old_definitions = load_definitions(args.old_v2_schema)
    new_definitions = load_definitions(args.new_v2_schema)
    old_full_methods = schema_methods(args.old_experimental_schema)
    old_stable_methods = schema_methods(args.old_stable_schema)
    new_full_methods = schema_methods(args.new_experimental_schema)
    new_stable_methods = schema_methods(args.new_stable_schema)

    contracts = []
    for method in sorted(cas_methods & experimental_methods):
        old_type = old_contracts[method]
        request_type = old_type["native_request_type"]
        response_type = old_type["native_response_type"]
        old_request = expand_schema(
            {"$ref": f"#/definitions/{request_type}"}, old_definitions
        )
        new_request = expand_schema(
            {"$ref": f"#/definitions/{request_type}"}, new_definitions
        )
        old_response = expand_schema(
            {"$ref": f"#/definitions/{response_type}"}, old_definitions
        )
        new_response = expand_schema(
            {"$ref": f"#/definitions/{response_type}"}, new_definitions
        )
        request_equal = canonical(old_request) == canonical(new_request)
        response_equal = canonical(old_response) == canonical(new_response)
        contracts.append(
            {
                "method": method,
                "request_type": request_type,
                "response_type": response_type,
                "request_contract": "unchanged" if request_equal else "changed",
                "response_contract": "unchanged" if response_equal else "changed",
                "request_sha256": {
                    args.old_version: contract_hash(old_request),
                    args.new_version: contract_hash(new_request),
                },
                "response_sha256": {
                    args.old_version: contract_hash(old_response),
                    args.new_version: contract_hash(new_response),
                },
            }
        )

    old_experimental_only = old_full_methods - old_stable_methods
    new_experimental_only = new_full_methods - new_stable_methods
    return {
        "comparison": f"Codex {args.old_version} to {args.new_version}",
        "source_commits": {
            args.old_version: args.old_commit,
            args.new_version: args.new_commit,
        },
        "generated_schema_sha256": {
            args.old_version: {
                args.old_experimental_schema.name: file_sha256(
                    args.old_experimental_schema
                ),
                args.old_v2_schema.name: file_sha256(args.old_v2_schema),
            },
            args.new_version: {
                args.new_experimental_schema.name: file_sha256(
                    args.new_experimental_schema
                ),
                args.new_v2_schema.name: file_sha256(args.new_v2_schema),
            },
        },
        "request_counts": {
            args.old_version: {
                "default": len(old_stable_methods),
                "experimental": len(old_full_methods),
            },
            args.new_version: {
                "default": len(new_stable_methods),
                "experimental": len(new_full_methods),
            },
        },
        "new_experimental_only_methods_not_exposed_by_cas": sorted(
            new_experimental_only - old_experimental_only - cas_methods
        ),
        "newly_experimental_methods": sorted(
            new_experimental_only - old_experimental_only
        ),
        "cas_experimental_contracts": contracts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rpc-source", type=Path, required=True)
    parser.add_argument("--old-contracts", type=Path, required=True)
    parser.add_argument("--old-version", required=True)
    parser.add_argument("--old-commit", required=True)
    parser.add_argument("--old-experimental-schema", type=Path, required=True)
    parser.add_argument("--old-stable-schema", type=Path, required=True)
    parser.add_argument("--old-v2-schema", type=Path, required=True)
    parser.add_argument("--new-version", required=True)
    parser.add_argument("--new-commit", required=True)
    parser.add_argument("--new-experimental-schema", type=Path, required=True)
    parser.add_argument("--new-stable-schema", type=Path, required=True)
    parser.add_argument("--new-v2-schema", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build_comparison(args), indent=2) + "\n")


if __name__ == "__main__":
    main()
