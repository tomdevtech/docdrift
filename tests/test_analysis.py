"""Tests for docdrift.core.analysis - the deterministic AST layer."""

from docdrift.core.analysis import (
    analyze_changes,
    build_project_model,
    file_outline,
    is_public,
    parse_module,
)

BIG = "    x = a + b\n    y = x * 2\n    z = y - 1\n    return [x, y, z, a, b]\n"


def kinds(cs, path):
    return [(c.kind, c.name) for c in cs.files[path].changes]


# --- parse_module ---------------------------------------------------------------


def test_parse_signature_keeps_kwonly_posonly_and_defaults():
    info = parse_module("m.py", "def f(a, /, b: int = 1, *args, c, d=2, **kw) -> str:\n    return ''\n")
    assert info.symbols["f"].signature == "def f(a, /, b: int=1, *args, c, d=2, **kw) -> str"


def test_parse_methods_nested_classes_and_decorators():
    src = "@dataclass\nclass A:\n    x: int = 0\n    def run(self): pass\n    class Inner:\n        def go(self): pass\n"
    symbols = parse_module("m.py", src).symbols
    assert list(symbols) == ["A", "A.run", "A.Inner", "A.Inner.go"]
    assert symbols["A"].signature == "@dataclass class A"
    assert symbols["A.run"].kind == "method"


def test_parse_non_python_and_syntax_error_are_unparsed():
    assert not parse_module("a.yaml", "x: 1").parsed
    broken = parse_module("a.py", "def (:")
    assert not broken.parsed and "could not be parsed" in broken.reason


def test_is_public():
    assert is_public("Foo.run") and is_public("Foo.__init__")
    assert not is_public("_helper") and not is_public("Foo._x")


def test_file_outline_lists_all_symbols_with_docs():
    out = file_outline("m.py", '"""Mod."""\ndef f(x):\n    """Do f."""\n    return x\n')
    assert out.splitlines() == ["Mod.", "def f(x)  # Do f."]


# --- analyze_changes: the bugs of the old implementation --------------------------


def test_body_only_change_is_detected_not_ignored():
    old = {"m.py": "def f(a, b):\n    return a + b\n"}
    new = {"m.py": "def f(a, b):\n    return a * b + 1\n"}
    cs = analyze_changes(old, new)
    assert kinds(cs, "m.py") == [("body", "f")]
    assert cs.files["m.py"].status == "changed"


def test_method_signature_change_is_detected():
    old = {"m.py": "class A:\n    def run(self, x):\n        return x\n"}
    new = {"m.py": "class A:\n    def run(self, x, y=1):\n        return x\n"}
    assert kinds(analyze_changes(old, new), "m.py") == [("signature", "A.run")]


def test_signature_and_body_both_changed_is_flagged():
    cs = analyze_changes({"m.py": "def f(a):\n    return a\n"}, {"m.py": "def f(a, b):\n    return b\n"})
    assert "implementation changed too" in cs.files["m.py"].changes[0].detail


def test_formatting_and_comments_only_is_unchanged():
    old = {"m.py": "def f(a):\n    return a+1\n"}
    new = {"m.py": "# note\ndef f( a ):\n    return a + 1  # inc\n"}
    assert analyze_changes(old, new).files["m.py"].status == "unchanged"


def test_module_level_constant_change_is_reported_as_other():
    cs = analyze_changes({"m.py": "TIMEOUT = 5\n"}, {"m.py": "TIMEOUT = 30\n"})
    assert kinds(cs, "m.py") == [("other", "module")]


def test_dataclass_field_change_is_reported():
    old = {"m.py": "@dataclass\nclass C:\n    a: int = 0\n"}
    new = {"m.py": "@dataclass\nclass C:\n    a: int = 0\n    b: str = ''\n"}
    cs = analyze_changes(old, new)
    assert kinds(cs, "m.py") == [("body", "C")]
    assert "fields" in cs.files["m.py"].changes[0].detail


def test_non_python_file_is_unsupported_not_unchanged():
    cs = analyze_changes({"c.yaml": "a: 1\n"}, {"c.yaml": "a: 2\n"})
    fc = cs.files["c.yaml"]
    assert fc.status == "unsupported"
    assert "rely on the diff" in cs.render("c.yaml")


def test_syntax_error_in_new_version_is_unsupported():
    cs = analyze_changes({"m.py": "x = 1\n"}, {"m.py": "def (:\n"})
    assert cs.files["m.py"].status == "unsupported"


def test_new_and_deleted_file():
    cs = analyze_changes({"old.py": "def gone():\n    pass\n"}, {"new.py": "def fresh():\n    pass\n"})
    assert cs.files["new.py"].status == "added" and kinds(cs, "new.py") == [("added", "fresh")]
    assert cs.files["old.py"].status == "removed" and kinds(cs, "old.py") == [("removed", "gone")]


def test_import_changes():
    cs = analyze_changes({"m.py": "import os\n"}, {"m.py": "import os\nfrom x import y\n"})
    assert ("import_added", "from x import y") in kinds(cs, "m.py")


# --- renames and moves ------------------------------------------------------------


def test_rename_in_same_file():
    old = {"m.py": f"def helper(a, b):\n{BIG}"}
    new = {"m.py": f"def compute(a, b):\n{BIG}"}
    cs = analyze_changes(old, new)
    assert kinds(cs, "m.py") == [("renamed", "compute")]
    assert "was helper" in cs.files["m.py"].changes[0].detail


def test_move_between_files():
    old = {"a.py": f"def util(a, b):\n{BIG}", "b.py": "X = 1\n"}
    new = {"a.py": "Y = 1\n", "b.py": f"X = 1\ndef util(a, b):\n{BIG}"}
    cs = analyze_changes(old, new)
    assert ("moved_in", "util") in kinds(cs, "b.py")
    assert ("moved_out", "util") in kinds(cs, "a.py")
    assert cs.count("moved_in") == 1


def test_trivial_bodies_are_not_paired_as_renames():
    cs = analyze_changes({"m.py": "def a():\n    pass\n"}, {"m.py": "def b():\n    pass\n"})
    assert sorted(kinds(cs, "m.py")) == [("added", "b"), ("removed", "a")]


def test_ambiguous_identical_bodies_are_not_paired():
    old = {"m.py": f"def a(a, b):\n{BIG}def b(a, b):\n{BIG}"}
    new = {"m.py": f"def c(a, b):\n{BIG}def d(a, b):\n{BIG}"}
    assert all(k in ("added", "removed") for k, _ in kinds(analyze_changes(old, new), "m.py"))


def test_public_api_files():
    cs = analyze_changes(
        {"a.py": "def f():\n    return 1\n", "b.py": "def _p():\n    return 1\n"},
        {"a.py": "def f(x):\n    return 1\n", "b.py": "def _p(x):\n    return 1\n"},
    )
    assert cs.public_api_files() == ["a.py"]


# --- project model ------------------------------------------------------------------

PROJECT = {
    "src/pkg/__init__.py": '"""Package."""\n',
    "src/pkg/cli.py": '"""CLI."""\nfrom pkg import core\nfrom .util import helper\nimport requests\n\ndef main():\n    core.run()\n',
    "src/pkg/core.py": '"""Core."""\nfrom . import util\n\ndef run():\n    util.helper()\n',
    "src/pkg/util.py": '"""Utils."""\ndef helper():\n    return 1\ndef _private():\n    return 2\n',
}


def test_dependencies_resolve_absolute_and_relative_imports_but_not_third_party():
    deps = build_project_model(PROJECT).dependencies()
    assert deps["src/pkg/cli.py"] == {"src/pkg/core.py", "src/pkg/util.py"}
    assert deps["src/pkg/core.py"] == {"src/pkg/util.py"}
    assert deps["src/pkg/util.py"] == set()


def test_entry_points_are_modules_nobody_imports():
    assert build_project_model(PROJECT).entry_points() == ["src/pkg/cli.py"]


def test_fingerprint_ignores_bodies_but_not_public_structure():
    base = build_project_model(PROJECT).fingerprint()
    body = dict(PROJECT, **{"src/pkg/util.py": '"""Utils."""\ndef helper():\n    return 99\ndef _private():\n    return 2\n'})
    private = dict(PROJECT, **{"src/pkg/util.py": '"""Utils."""\ndef helper():\n    return 1\ndef _private(x):\n    return 2\n'})
    api = dict(PROJECT, **{"src/pkg/util.py": '"""Utils."""\ndef helper(x):\n    return 1\ndef _private():\n    return 2\n'})
    assert build_project_model(body).fingerprint() == base
    assert build_project_model(private).fingerprint() == base
    assert build_project_model(api).fingerprint() != base


def test_outline_shows_public_api_only_and_shrinks_to_limit():
    model = build_project_model(PROJECT)
    outline = model.render_outline()
    assert "def helper()" in outline and "_private" not in outline
    assert "imports: src/pkg/core.py, src/pkg/util.py" in outline
    assert len(model.render_outline(max_chars=60)) <= 60 + len("\n... (outline truncated)")


def test_mermaid_contains_nodes_and_edges():
    mermaid = build_project_model(PROJECT).render_mermaid()
    assert mermaid.startswith("flowchart LR")
    assert 'pkg.cli' in mermaid and "-->" in mermaid
