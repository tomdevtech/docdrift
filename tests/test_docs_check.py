"""Tests for docdrift.docs_check: the pre-check of the merge flow."""

from pathlib import Path

from docdrift.analysis import analyze_changes
from docdrift.docs_check import check_docs, find_mentions, is_doc_file
from docdrift.git_utils import Changes

OLD = 'def run_it(path):\n    return 1\n\ndef _helper():\n    return 1\n'


def _check(old, new, changes, messages=(), docs=None):
    return check_docs(analyze_changes(old, new), changes, list(messages), docs or {})


def test_doc_file_selection():
    paths = ["docs/", "README.md"]
    assert is_doc_file("README.md", paths) and is_doc_file("docs/guide/setup.md", paths)
    assert not is_doc_file("docs/adr/0001-x.md", paths)  # ADRs are not product docs
    assert not is_doc_file("docs/diagram.png", paths) and not is_doc_file("src/a.md", paths)


def test_private_and_body_only_changes_are_internal():
    new = OLD.replace("return 1\n\ndef _helper", "return 2\n\ndef _helper").replace("def _helper():", "def _helper(x):")
    result = _check({"pkg/a.py": OLD}, {"pkg/a.py": new}, Changes(modified=[Path("pkg/a.py")]))
    assert not result.needed


def test_public_signature_change_is_a_reason():
    new = OLD.replace("def run_it(path)", "def run_it(path, *, force=False)")
    result = _check({"pkg/a.py": OLD}, {"pkg/a.py": new}, Changes(modified=[Path("pkg/a.py")]))
    assert result.reasons == ["public API of `pkg/a.py` changed: `run_it`"]


def test_private_module_is_internal():
    result = _check({}, {"pkg/_internal.py": "def x():\n    return 1\n"}, Changes(added=[Path("pkg/_internal.py")]))
    assert not result.needed


def test_new_public_module_manifest_opaque_files_and_feature_commit():
    changes = Changes(
        added=[Path("pkg/new.py"), Path("templates/hook.sh")],
        modified=[Path("pyproject.toml"), Path(".gitignore"), Path("LICENSE")],
    )
    result = _check(
        {"pyproject.toml": "a", ".gitignore": "a", "LICENSE": "a"},
        {"pkg/new.py": "def f():\n    return 1\n", "templates/hook.sh": "echo", "pyproject.toml": "b",
         ".gitignore": "b", "LICENSE": "b"},
        changes, ["feat: new command"],
    )
    text = "\n".join(result.reasons)
    assert "public module `pkg/new.py` added" in text
    assert "manifest changed" in text and "`pyproject.toml`" in text
    assert "`templates/hook.sh`" in text and ".gitignore" not in text and "LICENSE" not in text
    assert "new feature" in text


def test_breaking_commit_is_a_reason_even_for_internal_code():
    new = OLD.replace("return 1\n\ndef _helper", "return 2\n\ndef _helper")
    result = _check({"pkg/a.py": OLD}, {"pkg/a.py": new}, Changes(modified=[Path("pkg/a.py")]), ["fix!: new default"])
    assert result.reasons == ["commit messages announce a breaking change"]


def test_mentions_find_inline_code_calls_fenced_blocks_and_file_names():
    docs = {
        "README.md": (
            "# Tool\n"
            "Use `run_it` to start.\n"          # 2: inline code
            "Then call run_it(path).\n"         # 3: call
            "We run it daily.\n"                # 4: prose, no hit
            "```python\n"
            "from pkg import run_it\n"          # 6: fenced code
            "```\n"
            "See pipeline.py for details.\n"    # 8: file name
            "The pipeline is fast.\n"           # 9: no hit
        )
    }
    names = {"run_it": "implementation changed", "pipeline.py": "file changed"}
    lines = [(m.line, m.name) for m in find_mentions(docs, names)]
    assert lines == [(2, "run_it"), (3, "run_it"), (6, "run_it"), (8, "pipeline.py")]


def test_body_change_of_documented_function_is_a_reason():
    new = OLD.replace("return 1\n\ndef _helper", "return 2\n\ndef _helper")
    docs = {"docs/usage.md": "Call `run_it(path)` once.\n"}
    result = _check({"pkg/a.py": OLD}, {"pkg/a.py": new}, Changes(modified=[Path("pkg/a.py")]), docs=docs)
    assert result.needed and result.mentions[0].render().startswith("- `docs/usage.md:1` mentions `run_it`")
