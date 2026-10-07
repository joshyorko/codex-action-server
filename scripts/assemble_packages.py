#!/usr/bin/env python3
"""Build immutable, self-contained split packages without replacing operator data."""

import argparse
import ast
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import tempfile

PACKAGES = ("codex-observe", "codex-control")
MARKER = ".cas-assembly.json"
ROOT = Path(__file__).resolve().parents[1]


def source_files(root: Path) -> list[Path]:
    files = [root / "package.yaml", *sorted((root / "src").rglob("*.py"))]
    if any(path.is_symlink() or not path.is_file() for path in files):
        raise ValueError("assembly_source_must_be_regular_files")
    return files


def source_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(root):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


def package_membership(root: Path) -> dict[str, frozenset[str]]:
    registry = runpy.run_path(str(root / "src/action_catalog_contract.py"))
    membership = registry["PACKAGE_ACTION_NAMES"]
    if (
        not membership[PACKAGES[0]]
        or not membership[PACKAGES[1]]
        or membership[PACKAGES[0]] & membership[PACKAGES[1]]
        or membership[PACKAGES[0]] | membership[PACKAGES[1]]
        != membership["codex-action-server"]
    ):
        raise ValueError("invalid_split_package_membership")
    return membership


def entrypoint(source: str, package: str, names: frozenset[str], all_names) -> str:
    tree = ast.parse(source)
    discovered = set()
    kept = []
    for node in tree.body:
        decorators = (
            node.decorator_list
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            else ()
        )
        action_calls = [
            deco
            for deco in decorators
            if isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Name)
            and deco.func.id == "action"
        ]
        if action_calls:
            if node.name in discovered or len(action_calls) != 1:
                raise ValueError("duplicate_action_wrapper")
            discovered.add(node.name)
            if node.name not in names:
                continue
            call = action_calls[0]
            package_args = [kw for kw in call.keywords if kw.arg == "package"]
            if (
                len(package_args) != 1
                or not isinstance(package_args[0].value, ast.Constant)
                or package_args[0].value.value != "codex-action-server"
            ):
                raise ValueError("compatibility_package_decorator_required")
            package_args[0].value = ast.Constant(value=package)
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__all__"
            for target in node.targets
        ):
            exports = ast.literal_eval(node.value)
            node.value = ast.List(
                elts=[
                    ast.Constant(value=name)
                    for name in exports
                    if name not in all_names or name in names
                ],
                ctx=ast.Load(),
            )
        kept.append(node)
    if discovered != set(all_names):
        raise ValueError("entrypoint_catalog_membership_mismatch")
    tree.body = kept
    return ast.unparse(ast.fix_missing_locations(tree)) + "\n"


def manifest(source: str, package: str) -> str:
    source, count = re.subn(
        r"(?m)^name: codex-action-server$", f"name: {package}", source
    )
    if count != 1 or not source.startswith("spec-version: v2\n"):
        raise ValueError("v2_compatibility_manifest_required")
    source = source.replace("  - tests\n", "")
    # Development tasks refer to checkout-only tests and scripts.
    source = re.sub(r"(?ms)^dev-dependencies:.*?(?=^packaging:|\Z)", "", source)
    return source


def artifact_hashes(output: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(output.rglob("*")):
        if path.is_symlink():
            raise ValueError("assembly_artifact_symlink")
        if path.is_file() and path.name != MARKER:
            hashes[path.relative_to(output).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashes


def verify_artifacts(output: Path, fingerprint: str) -> None:
    if output.is_symlink() or not output.is_dir() or (output / MARKER).is_symlink():
        raise ValueError("assembly_output_must_be_directory")
    try:
        record = json.loads((output / MARKER).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError("assembly_output_is_not_owned_artifact") from exc
    if (
        record.get("format") != 1
        or record.get("source_sha256") != fingerprint
        or record.get("files") != artifact_hashes(output)
    ):
        raise ValueError("assembly_output_differs_use_new_directory")


def publish(staging: Path, output: Path) -> None:
    """Linux atomic directory publication with RENAME_NOREPLACE."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging), -100, os.fsencode(output), 1):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(output))


def assemble(root: Path, output: Path) -> Path:
    if not output.is_absolute() or output.is_symlink():
        raise ValueError("absolute_non_symlink_assembly_output_required")
    fingerprint = source_fingerprint(root)
    if output.exists():
        verify_artifacts(output, fingerprint)
        return output
    membership = package_membership(root)
    source = (root / "src/codex_actions.py").read_text()
    wrappers = {
        package: entrypoint(
            source, package, membership[package], membership["codex-action-server"]
        )
        for package in PACKAGES
    }
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=".cas-assembly-", dir=output.parent))
    try:
        for package in PACKAGES:
            artifact = staging / package
            artifact.mkdir()
            for path in source_files(root):
                destination = artifact / path.relative_to(root)
                destination.parent.mkdir(parents=True, exist_ok=True)
                if path.name == "package.yaml":
                    destination.write_text(manifest(path.read_text(), package))
                elif path == root / "src/codex_actions.py":
                    destination.write_text(wrappers[package])
                else:
                    shutil.copyfile(path, destination)
        record = {
            "format": 1,
            "source_sha256": fingerprint,
            "files": artifact_hashes(staging),
        }
        (staging / MARKER).write_text(json.dumps(record, sort_keys=True) + "\n")
        if source_fingerprint(root) != fingerprint:
            raise ValueError("assembly_source_changed_retry_with_stable_source")
        # Rename publishes only a complete tree; an existing destination is never removed.
        publish(staging, output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        assemble(ROOT, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Package assembly failed: {exc}\n")


if __name__ == "__main__":
    main()
