"""Self-contained artifacts and non-destructive repeatable assembly."""

import ast
import importlib.util
from pathlib import Path
import shutil

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_assembler():
    spec = importlib.util.spec_from_file_location(
        "cas_package_assembler", ROOT / "scripts/assemble_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "checkout"
    (root / "src/codex_shared").mkdir(parents=True)
    (root / "src/codex_shared/__init__.py").write_text("")
    (root / "src/codex_shared/models.py").write_text("class Request: pass\n")
    (root / "src/action_catalog_contract.py").write_text(
        "PACKAGE_ACTION_NAMES = {'codex-observe': frozenset({'read'}), "
        "'codex-control': frozenset({'write'}), "
        "'codex-action-server': frozenset({'read', 'write'})}\n"
    )
    (root / "src/codex_actions.py").write_text(
        '"""Endpoints."""\nfrom capability_registration import action\n'
        "from codex_shared.models import Request\n"
        "@action(package='codex-action-server')\n"
        "def read(request: Request) -> str:\n"
        '    """Read the value."""\n    return "read"\n'
        "@action(package='codex-action-server')\n"
        "def write(request: Request) -> str:\n"
        '    """Write the value."""\n    return "write"\n'
        "__all__ = ['Request', 'read', 'write']\n"
    )
    (root / "src/capability_registration.py").write_text("# shared policy\n")
    shutil.copy(ROOT / "package.yaml", root / "package.yaml")
    return root


def action_nodes(path):
    return {
        node.name: node
        for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef)
    }


def test_artifacts_are_self_contained_and_preserve_typed_wrappers(source, tmp_path):
    output = tmp_path / "artifacts"
    load_assembler().assemble(source, output)
    original = action_nodes(source / "src/codex_actions.py")
    for package, name in [("codex-observe", "read"), ("codex-control", "write")]:
        artifact = output / package
        nodes = action_nodes(artifact / "src/codex_actions.py")
        assert set(nodes) == {name}
        module = ast.parse((artifact / "src/codex_actions.py").read_text())
        exported = next(
            node.value
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name) and target.id == "__all__"
                for target in node.targets
            )
        )
        assert ast.literal_eval(exported) == ["Request", name]
        assert ast.dump(nodes[name].args) == ast.dump(original[name].args)
        assert ast.dump(nodes[name].returns) == ast.dump(original[name].returns)
        assert ast.get_docstring(nodes[name]) == ast.get_docstring(original[name])
        assert nodes[name].decorator_list[0].keywords[0].value.value == package
        manifest = (artifact / "package.yaml").read_text()
        assert f"name: {package}\n" in manifest
        assert "spec-version: v2" in manifest
        assert "  - tests" not in manifest
        assert str(source) not in manifest
        assert (artifact / "src/codex_shared/models.py").is_file()
        assert (artifact / "src/capability_registration.py").is_file()
    shutil.rmtree(source)
    assert (output / "codex-observe/src/codex_shared/models.py").is_file()


def test_repeated_assembly_retains_identical_artifact(source, tmp_path):
    output = tmp_path / "artifacts"
    assembler = load_assembler()
    assembler.assemble(source, output)
    before = (output / "codex-observe/package.yaml").stat().st_mtime_ns
    assembler.assemble(source, output)
    assert (output / "codex-observe/package.yaml").stat().st_mtime_ns == before
    (output / "codex-observe/src/codex_actions.py").write_text("tampered")
    with pytest.raises(ValueError):
        assembler.assemble(source, output)


def test_assembly_never_overwrites_operator_directory(source, tmp_path):
    output = tmp_path / "operator"
    output.mkdir()
    retained = output / "data"
    retained.write_text("keep me")
    with pytest.raises(ValueError):
        load_assembler().assemble(source, output)
    assert retained.read_text() == "keep me"


def test_assembly_rejects_relative_and_symlink_destinations(source, tmp_path):
    assembler = load_assembler()
    with pytest.raises(ValueError):
        assembler.assemble(source, Path("relative"))
    output = tmp_path / "link"
    output.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ValueError):
        assembler.assemble(source, output)


def test_missing_membership_fails_without_partial_artifacts(source, tmp_path):
    actions = source / "src/codex_actions.py"
    actions.write_text(actions.read_text().replace("def write(", "def other("))
    output = tmp_path / "artifacts"
    with pytest.raises(ValueError):
        load_assembler().assemble(source, output)
    assert not output.exists()


def test_publication_never_replaces_even_empty_operator_directory(tmp_path):
    assembler = load_assembler()
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "artifact").write_text("generated")
    operator = tmp_path / "operator"
    operator.mkdir()
    before = operator.stat().st_ino
    with pytest.raises(OSError):
        assembler.publish(staging, operator)
    assert operator.stat().st_ino == before
    assert not list(operator.iterdir())


def test_baked_artifacts_are_excluded_from_compatibility_discovery(source):
    from actions._collect_actions import FindActionPaths

    load_assembler().assemble(source, source / "packages")
    discovered = list(FindActionPaths(source, ["*action*.py"], []))
    assert source / "src/codex_actions.py" in discovered
    assert not any("packages" in path.relative_to(source).parts for path in discovered)
