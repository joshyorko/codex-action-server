"""Assertions for pinned native App Server wire-contract fixtures."""

import json
from pathlib import Path


CONTRACTS = json.loads(
    (
        Path(__file__).parent
        / "fixtures/protocol/codex_0.160.1_operator_rpc_contracts.json"
    ).read_text()
)["contracts"]


def _matches_type(value, expected):
    types = expected if isinstance(expected, list) else [expected]
    return any(
        (kind == "null" and value is None)
        or (kind == "string" and isinstance(value, str))
        or (kind == "boolean" and isinstance(value, bool))
        or (
            kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
        )
        or (
            kind == "number"
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
        )
        or (kind == "array" and isinstance(value, list))
        or (kind == "object" and isinstance(value, dict))
        or kind == "any"
        for kind in types
    )


def assert_contract_value(schema, value):
    if "oneOf" in schema:
        valid = 0
        for variant in schema["oneOf"]:
            try:
                assert_contract_value(variant, value)
            except AssertionError:
                continue
            valid += 1
        assert valid == 1
        return
    if "anyOf" in schema:
        valid = 0
        for variant in schema["anyOf"]:
            try:
                assert_contract_value(variant, value)
            except AssertionError:
                continue
            valid += 1
        assert valid >= 1
        return
    if "type" in schema:
        assert _matches_type(value, schema["type"])
    if "enum" in schema:
        assert value in schema["enum"]
    if not isinstance(value, dict):
        return
    properties = schema.get("properties", {})
    assert set(schema.get("required", ())) <= value.keys()
    for name, item in value.items():
        if name not in properties:
            continue
        field = properties[name]
        if "type" in field:
            assert _matches_type(item, field["type"])
        if "enum" in field:
            assert item in field["enum"]
        if isinstance(item, dict):
            assert_contract_value(field, item)
        elif isinstance(item, list) and isinstance(field.get("items"), dict):
            for element in item:
                assert_contract_value(field["items"], element)


def assert_native_request_contract(method, params):
    if method == "turn/start" and params.get("sandboxPolicy") is not None:
        # The older reduced fixture lost discriminators and made every object
        # match all four oneOf branches. Validate the exact current native shape.
        policy_schema = json.loads(
            (
                Path(__file__).parent
                / "fixtures/protocol/codex_0.162.0_execution_policy.json"
            ).read_text()
        )["SandboxPolicy"]
        assert_contract_value(policy_schema, params["sandboxPolicy"])
        params = {key: value for key, value in params.items() if key != "sandboxPolicy"}
    contract = CONTRACTS[method]
    if contract["native_request_contract_scope"] == "cas-safe-subset":
        assert isinstance(params, dict)
        unexpected = set(params) - set(contract["params"].get("properties", {}))
        assert not unexpected, (
            f"{method} emitted native fields outside its CAS-safe subset: "
            f"{sorted(unexpected)}"
        )
    assert_contract_value(contract["params"], params)


def assert_native_response_contract(method, response):
    assert_contract_value(CONTRACTS[method]["response"], response)
