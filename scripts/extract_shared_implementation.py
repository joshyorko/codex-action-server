#!/usr/bin/env python3
"""Extract the pre-composition entrypoint without changing implementation ASTs.

Pass a retained baseline source with --source and a destination checkout with
--output. --check compares the extraction to an existing destination instead of
writing it. The verifier tolerates formatting changes, but not behavioral edits.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import symtable

GROUP_ACTIONS = {
    "inspection": "read_dispatch_receipt list_targets inspect_target list_native_capabilities list_models read_model_provider_capabilities read_server_diagnostics read_account_rate_limits read_account_usage".split(),
    "threads": "discover_threads list_thread_sections search_threads search_thread_occurrences list_thread_timeline list_thread_turns list_thread_items read_thread get_thread_snapshot list_loaded_threads".split(),
    "execution": "start_review update_thread_settings update_turn_settings get_thread_goal set_thread_goal clear_thread_goal start_thread create_thread_and_start_turn resume_thread start_turn steer_turn interrupt_turn".split(),
    "organization": "fork_thread archive_thread unarchive_thread delete_thread set_thread_name update_thread_metadata revert_thread compact_thread list_background_terminals terminate_background_terminal list_thread_attachments add_thread_attachment remove_thread_attachment inject_thread_items create_thread_section update_thread_section delete_thread_section move_thread_to_section".split(),
    "queues": "list_thread_queue add_thread_queue_item update_thread_queue_item delete_thread_queue_item reorder_thread_queue start_thread_queue".split(),
    "integrations": "list_skills list_hooks list_plugins read_plugin list_apps read_app read_mcp_resource call_mcp_tool list_mcp_server_status".split(),
}
GROUP_HELPERS = {
    "threads": "_utf8_truncate _snapshot_error_code _snapshot_unknown_state_digest _user_message_excerpt _snapshot_item _project_thread_snapshot _snapshot_response SNAPSHOT_MAX_BYTES SNAPSHOT_MESSAGE_MAX_BYTES _THREAD_STATUS_TYPES _TURN_STATUS_TYPES _ACTIVE_FLAGS _ERROR_CODES".split(),
    "execution": ["_apply_thread_effort", "_goal_result"],
    "organization": ["_thread_control"],
    "queues": ["_thread_queue_control"],
    "integrations": ["_validate_app_read_response"],
}


def bindings(node):
    if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
        return [node.name]
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        return [target.id for target in targets if isinstance(target, ast.Name)]
    return []


def referenced_globals(source):
    names = set()

    def visit(table):
        names.update(
            symbol.get_name()
            for symbol in table.get_symbols()
            if symbol.is_global() and symbol.is_referenced()
        )
        for child in table.get_children():
            visit(child)

    visit(symtable.symtable(source, "<extraction>", "exec"))
    # Future annotations are strings at runtime but require explicit imports for
    # the native runtime's schema discovery and get_type_hints.
    for node in ast.walk(ast.parse(source)):
        annotations = []
        if isinstance(node, ast.arg) and node.annotation:
            annotations.append(node.annotation)
        if isinstance(node, ast.FunctionDef) and node.returns:
            annotations.append(node.returns)
        if isinstance(node, ast.AnnAssign):
            annotations.append(node.annotation)
        for annotation in annotations:
            names.update(n.id for n in ast.walk(annotation) if isinstance(n, ast.Name))
    return names


def extract(source):
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)
    imports = {}
    nodes = defaultdict(list)
    owners = {}
    actions = []
    action_groups = {
        name: group for group, names in GROUP_ACTIONS.items() for name in names
    }
    helper_groups = {
        name: group for group, names in GROUP_HELPERS.items() for name in names
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports[alias.asname or alias.name] = f"import {alias.name}" + (
                    f" as {alias.asname}" if alias.asname else ""
                )
            continue
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                imports[alias.asname or alias.name] = (
                    f"from {node.module} import {alias.name}"
                    + (f" as {alias.asname}" if alias.asname else "")
                )
            continue
        names = bindings(node)
        if not names or names[0] in {
            "action",
            "_ALL_ACTION_NAMES",
            "_PROFILE_ACTION_NAMES",
        }:
            continue
        name = names[0]
        decorated = isinstance(node, ast.FunctionDef) and bool(node.decorator_list)
        if decorated:
            if name not in action_groups:
                raise ValueError(f"Missing action group: {name}")
            actions.append(node)
            group = action_groups[name]
        elif isinstance(node, ast.ClassDef) and name != "_UnkeyedReceipt":
            group = "models"
        elif name in {
            "TargetName",
            "SortDirection",
            "TurnItemsView",
            "ThreadSearchSortKey",
            "ThreadSourceKind",
            "ThreadGoalStatus",
            "ReviewTarget",
        }:
            group = "models"
        else:
            group = helper_groups.get(name, "common")
        nodes[group].append(node)
        owners.update({name: group for name in names})
    if set(action_groups) != {node.name for node in actions}:
        raise ValueError("Action grouping differs from the baseline catalog")
    generated = {
        "src/codex_shared/__init__.py": '"""Undecorated Codex implementations shared by selectable action packages."""\n'
    }
    for group, members in sorted(nodes.items()):
        body = "\n\n".join(
            "".join(lines[node.lineno - 1 : node.end_lineno]).rstrip()
            for node in members
        )
        needed = referenced_globals(body) - {
            name for node in members for name in bindings(node)
        }
        declarations = []
        for name in sorted(needed):
            if name in owners:
                declarations.append(f"from codex_shared.{owners[name]} import {name}")
            elif name in imports:
                declarations.append(imports[name])
        generated[f"src/codex_shared/{group}.py"] = (
            f'"""Shared {group} behavior; importing this module registers no actions."""\n\nfrom __future__ import annotations\n\n'
            + "\n".join(declarations)
            + "\n\n\n"
            + body
            + "\n"
        )
    model_names = sorted(name for name, group in owners.items() if group == "models")
    wrapper_imports = [
        "from actions import Response",
        "from typing import Any",
        "from capability_registration import action",
    ]
    wrapper_imports += [
        f"from codex_shared import {group}" for group in sorted(GROUP_ACTIONS)
    ]
    wrapper_imports += [
        f"from codex_shared.models import {name} as {name}" for name in model_names
    ]
    wrappers = []
    for node in actions:
        header = "".join(lines[node.lineno - 1 : node.body[0].lineno - 1]).rstrip()
        doc = node.body[0]
        if (
            not isinstance(doc, ast.Expr)
            or not isinstance(doc.value, ast.Constant)
            or not isinstance(doc.value.value, str)
        ):
            raise ValueError(f"Missing action docstring: {node.name}")
        doc_source = "".join(lines[doc.lineno - 1 : doc.end_lineno]).rstrip()
        args = ", ".join(arg.arg for arg in node.args.args)
        wrappers.append(
            f'@action(package="codex-action-server")\n{header}\n{doc_source}\n    return {action_groups[node.name]}.{node.name}({args})'
        )
    generated["src/codex_actions.py"] = (
        '"""Typed compatibility entrypoints for the Codex Action Server package."""\n\nfrom __future__ import annotations\n\n'
        + "\n".join(wrapper_imports)
        + "\n\n\n"
        + "\n\n\n".join(wrappers)
        + "\n"
    )
    return generated


def definition_digest(nodes):
    """Hash semantic fields independently of Python's changing AST repr defaults."""

    def encode(value):
        if isinstance(value, ast.AST):
            return {
                "node": type(value).__name__,
                **{
                    name: encode(field)
                    for name, field in ast.iter_fields(value)
                    if field is not None and field != []
                },
            }
        if isinstance(value, list):
            return [encode(item) for item in value]
        if isinstance(value, bytes):
            return {"bytes": value.hex()}
        return value

    definitions = sorted(json.dumps(encode(node), sort_keys=True) for node in nodes)
    return hashlib.sha256("\n".join(definitions).encode()).hexdigest()


def normalized(source):
    tree = ast.parse(source)
    # Import order/grouping and whitespace do not change the extraction contract.
    imports = []
    definitions = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                imports.append(
                    (getattr(node, "module", None), alias.name, alias.asname)
                )
        else:
            definitions.append(ast.dump(node, include_attributes=False))
    return sorted(imports, key=repr), definitions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    generated = extract(args.source.read_text())
    for relative, source in generated.items():
        destination = args.output / relative
        if args.check:
            if normalized(destination.read_text()) != normalized(source):
                raise SystemExit(f"Extraction differs from baseline: {relative}")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source)
    print(f"{'Verified' if args.check else 'Extracted'} {len(generated)} source files")


if __name__ == "__main__":
    main()
