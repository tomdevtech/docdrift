"""Tests for docdrift.local.adr: significance scoring, draft parsing and numbering."""

from pathlib import Path

import pytest

from docdrift.core.analysis import analyze_changes
from docdrift.core.config import AdrConfig, DocdriftConfig, GithubDocsConfig, ObsidianConfig
from docdrift.core.git_utils import Changes
from docdrift.local import adr
from docdrift.local.state import State
from docdrift.local.vault import LocalVault


def _changeset(old=None, new=None):
    return analyze_changes(old or {}, new or {})


def test_small_body_change_scores_zero():
    cs = _changeset({"a.py": "def f():\n    return 1\n"}, {"a.py": "def f():\n    return 2\n"})
    assert adr.assess(cs, Changes(modified=[Path("a.py")]), ["fix: typo"]) == []


def test_new_module_plus_feature_commit_reaches_default_threshold():
    cs = _changeset(new={"b.py": "def g():\n    return 1\n"})
    signals = adr.assess(cs, Changes(added=[Path("b.py")]), ["feat: add b"])
    assert adr.score(signals) == 4


def test_dependency_manifest_counts():
    signals = adr.assess(_changeset(), Changes(modified=[Path("pyproject.toml")]), [])
    assert adr.score(signals) == 3
    signals = adr.assess(_changeset(), Changes(modified=[Path("requirements-dev.txt")]), [])
    assert adr.score(signals) == 3


def test_breaking_commit_beats_feature_keyword():
    for msg in ("feat!: drop api", "refactor(core)!: x", "BREAKING CHANGE: y"):
        assert adr.score(adr.assess(_changeset(), Changes(), [msg])) == 3
    assert adr.score(adr.assess(_changeset(), Changes(), ["we migrate to a new design"])) == 2


def test_public_api_in_three_files_and_moves_count():
    old = {f"{n}.py": "def f():\n    return 1\n" for n in "abc"}
    new = {f"{n}.py": "def f(x):\n    return 1\n" for n in "abc"}
    signals = adr.assess(_changeset(old, new), Changes(modified=[Path(f"{n}.py") for n in "abc"]), [])
    assert [s.points for s in signals] == [2]


def test_many_files_add_a_point():
    changes = Changes(modified=[Path(f"f{i}.txt") for i in range(10)])
    assert adr.score(adr.assess(_changeset(), changes, [])) == 1


GOOD = "Title: Use AST analysis\n\n## Context\nDiffs are noisy.\n\n## Decision\nParse with ast.\n\n## Consequences\n- Python only\n"


def test_parse_draft_ok():
    draft = adr.parse_draft(GOOD)
    assert draft.title == "Use AST analysis"
    assert draft.context == "Diffs are noisy." and draft.consequences == "- Python only"


def test_parse_draft_accepts_markdown_heading_title():
    assert adr.parse_draft(GOOD.replace("Title: ", "# ADR 0007: ")).title == "Use AST analysis"


@pytest.mark.parametrize("broken", ["no sections at all", GOOD.replace("## Decision", "## Plan"), "## Context\nx\n## Decision\ny\n## Consequences\nz"])
def test_parse_draft_rejects_incomplete_replies(broken):
    with pytest.raises(ValueError):
        adr.parse_draft(broken)


def test_slugify():
    assert adr.slugify("Use AST-analysis, then LLM!") == "use-ast-analysis-then-llm"
    assert adr.slugify("???") == "decision"


def _cfg(tmp_path):
    return DocdriftConfig(
        repo_name="proj", api_base_url="", model="", obsidian=ObsidianConfig("local"),
        github_docs=GithubDocsConfig(), adr=AdrConfig(),
    )


class _LLM:
    def chat(self, messages, temperature=0.2):
        return GOOD


def _draft(tmp_path, st):
    vault = LocalVault(tmp_path / "vault")
    where = adr.create_draft(
        repo_root=tmp_path, cfg=_cfg(tmp_path), client=_LLM(), vault=vault, st=st,
        changeset=_changeset(), commit_messages=["feat: x"], signals=[adr.Signal(3, "reason")],
        from_sha="a" * 40, to_sha="b" * 40,
    )
    return where, vault


def test_create_draft_in_obsidian_numbers_sequentially_and_writes_index(tmp_path):
    st = State()
    first, vault = _draft(tmp_path, st)
    second, _ = _draft(tmp_path, st)
    assert first == "Projects/proj/ADR/0001-use-ast-analysis.md"
    assert second == "Projects/proj/ADR/0002-use-ast-analysis.md"
    meta, body = vault.read_note(first)
    assert meta["status"] == "proposed" and meta["adr"] == 1
    assert "# ADR 0001: Use AST analysis" in body and "reason" in body
    _, index = vault.read_note("Projects/proj/ADR/Index.md")
    assert "[[0001-use-ast-analysis]]" in index and "[[0002-use-ast-analysis]]" in index


def test_drafts_never_touch_the_repository(tmp_path):
    _draft(tmp_path, State())
    assert not (tmp_path / "docs").exists()


def test_numbering_survives_a_lost_state(tmp_path):
    """New machine or CI runner: the vault index still knows which numbers are taken."""
    _draft(tmp_path, State())
    _draft(tmp_path, State())
    third, vault = _draft(tmp_path, State())  # fresh state every time
    assert third == "Projects/proj/ADR/0003-use-ast-analysis.md"
    _, index = vault.read_note("Projects/proj/ADR/Index.md")
    assert all(f"[[000{n}-use-ast-analysis]]" in index for n in (1, 2, 3))


def test_manual_notes_below_the_index_marker_survive(tmp_path):
    st = State()
    _draft(tmp_path, st)
    path = tmp_path / "vault" / "Projects/proj/ADR/Index.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nmy own remark\n", encoding="utf-8")
    _draft(tmp_path, st)
    assert "my own remark" in path.read_text(encoding="utf-8")


def test_unparseable_reply_writes_nothing(tmp_path):
    class Bad:
        def chat(self, messages, temperature=0.2):
            return "I cannot do that"

    st = State()
    with pytest.raises(ValueError):
        adr.create_draft(
            repo_root=tmp_path, cfg=_cfg(tmp_path), client=Bad(), vault=LocalVault(tmp_path / "v"),
            st=st, changeset=_changeset(), commit_messages=[], signals=[], from_sha="a", to_sha="b",
        )
    assert st.adrs == [] and not (tmp_path / "v").exists()
