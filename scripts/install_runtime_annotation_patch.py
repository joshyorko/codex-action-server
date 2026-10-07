#!/usr/bin/env python3
"""Project CAS tool policy through the exact PyPI runtime MCP adapter."""

import ast
import hashlib
import inspect
from pathlib import Path
import sys
from importlib.metadata import distribution, version


RUNTIME_VERSION = "1.0.2"
SDK_VERSION = "2.0.0"
SOURCE_SHA256 = "d8d8cf0914419c3e2037b81339d089b3cbdc1900f7dd3027dc8751d19ac73898"

# Import only the standard-library metadata at build/install time. The injected
# adapter policy below contains serialized records and no CAS/Core imports.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from action_catalog_contract import CAPABILITIES, PACKAGE_ACTION_NAMES  # noqa: E402

READ_ONLY_TOOLS = frozenset(
    name for name, capability in CAPABILITIES.items() if capability.read_only_hint
)
CONTROL_TOOLS = frozenset(
    name for name, capability in CAPABILITIES.items() if capability.is_consequential
)
REVIEWED_ANNOTATIONS = {
    (package, "src/codex_actions.py", name): {
        "read_only_hint": capability.read_only_hint,
        "destructive_hint": capability.destructive_hint,
        "idempotent_hint": capability.idempotent_hint,
    }
    for name, capability in CAPABILITIES.items()
    for package in sorted(capability.packages)
}
REVIEWED_PACKAGE_FILES = frozenset(
    (package, "src/codex_actions.py") for package in PACKAGE_ACTION_NAMES
)

ORIGINAL = "        options = json.loads(action.options) if action.options else {}\n"
REPLACEMENT = (
    ORIGINAL
    + """        options = annotation_options(
            action_package.name, action.file, action.name, options
        )
"""
)


def annotation_options(package_name, file, name, options):
    if (package_name, file) not in REVIEWED_PACKAGE_FILES or options.get(
        "kind", "action"
    ) != "action":
        return options
    projected = dict(options)
    projected.update(
        REVIEWED_ANNOTATIONS.get(
            (package_name, file, name),
            {
                "read_only_hint": False,
                "destructive_hint": True,
                "idempotent_hint": False,
            },
        )
    )
    return projected


def patch_source(source, runtime_version, sdk_version):
    if runtime_version != RUNTIME_VERSION or sdk_version != SDK_VERSION:
        raise RuntimeError("unsupported_runtime_or_sdk_version")
    if hashlib.sha256(source.encode()).hexdigest() != SOURCE_SHA256:
        raise RuntimeError("unexpected_runtime_adapter_source")
    if source.count(ORIGINAL) != 1:
        raise RuntimeError("unexpected_runtime_adapter_shape")
    policy = (
        f"\n\nREVIEWED_ANNOTATIONS = {REVIEWED_ANNOTATIONS!r}\n"
        f"REVIEWED_PACKAGE_FILES = frozenset({tuple(sorted(REVIEWED_PACKAGE_FILES))!r})\n\n"
        + inspect.getsource(annotation_options)
    )
    patched = source.replace(ORIGINAL, REPLACEMENT) + policy
    ast.parse(patched)
    return patched


def main():
    runtime = distribution("actions-runtime")
    path = runtime.locate_file("actions/server/mcp/setup_mcp_server_v2.py")
    patched = patch_source(path.read_text(), runtime.version, version("mcp"))
    path.write_text(patched)
    print("Installed scoped CAS MCP annotations for actions-runtime 1.0.2")


if __name__ == "__main__":
    main()
