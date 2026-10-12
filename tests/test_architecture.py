"""Guards the package layout: every module belongs to a feature, flows stay separate.

    core/   shared: config, git, AST, change detection -> imports no other feature
    local/  technical docs (Obsidian, local LLM)       -> never merge/, setup/, cli/
    merge/  product docs (Copilot, no LLM)             -> never local/, setup/, cli/
    setup/  install + `docdrift check`                 -> never cli/
    cli/    commands only                              -> may use everything
"""

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "docdrift"

FORBIDDEN = {
    "core": ("docdrift.local", "docdrift.merge", "docdrift.setup", "docdrift.cli"),
    "local": ("docdrift.merge", "docdrift.setup", "docdrift.cli"),
    "merge": ("docdrift.local", "docdrift.setup", "docdrift.cli"),
    "setup": ("docdrift.cli",),
    "cli": (),
}


def _imports(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("package", sorted(FORBIDDEN))
def test_flows_are_strictly_separated(package):
    violations = [
        f"{path.relative_to(PACKAGE)} imports {name}"
        for path in sorted((PACKAGE / package).glob("*.py"))
        for name in sorted(_imports(path))
        if name.startswith(FORBIDDEN[package])
    ]
    assert not violations, "\n".join(violations)


def test_templates_live_with_their_flow():
    assert (PACKAGE / "local" / "hooks" / "pre-push.template").exists()
    assert (PACKAGE / "local" / "playbooks" / "base.md").exists()
    assert (PACKAGE / "local" / "github_workflows" / "obsidian-docs.yml.template").exists()
    assert (PACKAGE / "merge" / "github_workflows" / "product-docs-on-merge.yml.template").exists()


def test_every_module_lives_in_a_feature_folder():
    """Only the package marker may sit at the top; everything else is in a feature."""
    top_level = sorted(p.name for p in PACKAGE.iterdir() if p.is_file())
    assert top_level == ["__init__.py"]
    features = {p.name for p in PACKAGE.iterdir() if p.is_dir() and not p.name.startswith("__")}
    assert features == set(FORBIDDEN)
    for feature in features:
        assert (PACKAGE / feature / "__init__.py").exists(), f"{feature}/ is not a package"
