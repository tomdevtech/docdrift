"""End-to-end tests of docdrift.local.pipeline with a real Git repo and a fake LLM."""

from pathlib import Path

import pytest
from conftest import FakeLLM

from docdrift.core.config import AdrConfig, DocdriftConfig, GithubDocsConfig, ObsidianConfig
from docdrift.local import pipeline, state
from docdrift.local.vault import AUTO_DOC_MARKER, LocalVault

A = '"""Entry."""\nfrom proj import b\n\ndef main():\n    return b.work()\n'
B = '"""Worker."""\ndef work():\n    return 1\n'


@pytest.fixture
def setup(repo, tmp_path):
    vault = LocalVault(tmp_path / "vault")
    cfg = DocdriftConfig(
        repo_name="proj", api_base_url="", model="", obsidian=ObsidianConfig("local"),
        github_docs=GithubDocsConfig(), ignore=["tests/"], adr=AdrConfig(threshold=99),
    )
    return repo, vault, cfg


def run(setup, llm, **kw):
    repo, vault, cfg = setup
    return pipeline.run_documentation(repo.root, cfg, client=llm, vault=vault, log=lambda m: None, **kw)


def test_first_run_documents_everything_and_builds_architecture(setup, llm):
    repo, vault, _ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B, "proj/__init__.py": "", "tests/t.py": "x=1\n"})
    result = run(setup, llm)

    assert result.ok and sorted(result.documented) == ["proj/__init__.py", "proj/a.py", "proj/b.py"]
    meta, body = vault.read_note("Projects/proj/proj/a.md")
    assert meta["project"] == "proj" and meta["file"] == "proj/a.py" and "updated" in meta
    assert body.startswith("## Purpose") and AUTO_DOC_MARKER in body  # fence stripped, marker added

    assert result.architecture_updated
    meta, arch = vault.read_note("Projects/proj/_Architecture.md")
    assert meta["type"] == "architecture" and "## Overview" in arch
    assert "```mermaid" in arch and "-->" in arch  # deterministic module map
    assert state.load_state(Path(repo.root) / ".docdrift" / "state", "proj").last_sha


def test_second_run_without_new_commits_does_nothing(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/a.py": A})
    run(setup, llm)
    calls = len(llm.prompts)
    result = run(setup, llm)
    assert result.up_to_date and len(llm.prompts) == calls


def test_formatting_only_commit_is_skipped_without_llm_call(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": B})
    run(setup, llm)
    calls = len(llm.prompts)
    repo.commit({"proj/b.py": B.replace("return 1", "return 1  # comment")})
    result = run(setup, llm)
    assert result.skipped == ["proj/b.py"] and result.documented == [] and len(llm.prompts) == calls


def test_body_change_reaches_the_prompt_but_does_not_regenerate_architecture(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B, "proj/__init__.py": ""})
    run(setup, llm)
    repo.commit({"proj/b.py": B.replace("return 1", "return 2")})
    result = run(setup, llm)
    prompt = llm.prompts[-1]
    assert "~ body work: implementation changed" in prompt and "-    return 1" in prompt
    assert result.documented == ["proj/b.py"] and not result.architecture_updated


def test_public_signature_change_regenerates_architecture(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B, "proj/__init__.py": ""})
    run(setup, llm)
    repo.commit({"proj/b.py": B.replace("work()", "work(n=1)")})
    assert run(setup, llm).architecture_updated


def test_failure_keeps_the_range_open_and_other_files_still_run(setup):
    repo, *_ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B})
    result = run(setup, FakeLLM(fail_on="File: proj/a.py"))
    assert result.failed == ["proj/a.py"] and result.documented == ["proj/b.py"]
    assert not result.architecture_updated  # later stages are skipped after a failure
    st = state.load_state(repo.root / ".docdrift" / "state", "proj")
    assert st.last_sha is None
    retry = run(setup, FakeLLM())
    assert retry.ok and "proj/a.py" in retry.documented


def test_large_file_is_replaced_by_outline_in_prompt(setup, llm):
    repo, _, cfg = setup
    big = "def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(3000)) + "    return 1\n"
    repo.commit({"proj/big.py": big})
    cfg.max_prompt_chars = 4000
    run(setup, llm)
    prompt = next(p for p in llm.prompts if "File: proj/big.py" in p)
    assert "signature outline only" in prompt and "x2999" not in prompt


def test_adr_is_drafted_once_for_a_significant_change(setup, llm):
    repo, vault, cfg = setup
    cfg.adr = AdrConfig(threshold=4)
    repo.commit({"proj/a.py": A}, "feat: first version")
    assert run(setup, llm).adr_drafts == []  # never for the initial import
    repo.commit({"proj/b.py": B}, "feat: add the worker module")
    result = run(setup, llm)
    assert result.adr_drafts == ["Projects/proj/ADR/0001-split-the-tool-into-modules.md"]
    meta, body = vault.read_note(result.adr_drafts[0])
    assert meta["status"] == "proposed" and "module(s) added" in body
    assert run(setup, llm).up_to_date  # no second draft for the same commit


def test_adr_not_drafted_below_threshold(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": B}, "fix: small")
    assert run(setup, llm).adr_drafts == []


def test_deleted_file_is_not_documented_and_binary_is_ignored(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": B, "img.bin": "\0\1\2"})
    run(setup, llm)
    repo.commit({"proj/b.py": None})
    result = run(setup, llm)
    assert result.documented == [] and not result.failed


def test_manual_adr_command_ignores_threshold(setup, llm):
    repo, vault, _ = setup
    first = repo.commit({"proj/b.py": B})
    second = repo.commit({"proj/b.py": B.replace("1", "2")})
    where = pipeline.draft_adr(repo.root, setup[2], from_sha=first, to_sha=second, client=llm, vault=vault, log=lambda m: None)
    assert where.startswith("Projects/proj/ADR/0001")
    with pytest.raises(ValueError, match="Empty range"):
        pipeline.draft_adr(repo.root, setup[2], from_sha=second, to_sha=second, client=llm, vault=vault, log=lambda m: None)


def test_should_ignore_and_parse_note():
    assert pipeline.should_ignore(Path("tests/x.py"), ["tests/"])
    assert pipeline.should_ignore(Path("a/b.lock"), ["*.lock"])
    assert not pipeline.should_ignore(Path("src/a.py"), ["tests/"])
    meta, body = pipeline.parse_note("---\nupdated: 1999-01-01\n---\n## X", {"project": "p"})
    assert meta["project"] == "p" and meta["updated"] != "1999-01-01" and body == "## X"


def test_deleted_file_gets_its_note_marked(setup, llm):
    repo, vault, _ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B})
    run(setup, llm)
    note = "Projects/proj/proj/b.md"
    path = vault.root / note
    path.write_text(path.read_text(encoding="utf-8") + "\nmy remark\n", encoding="utf-8")
    repo.commit({"proj/b.py": None}, "remove b")
    result = run(setup, llm)
    assert result.deleted == ["proj/b.py"] and result.ok
    meta, body = vault.read_note(note)
    assert meta["deleted"] and "deleted" in meta["tags"]
    assert body.startswith(pipeline.DELETED_NOTICE) and "## Purpose" in body and "my remark" in body


def test_retry_after_failure_only_redoes_the_failed_file(setup):
    """A failed run must not send every file through the LLM again."""
    repo, *_ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B})
    flaky = FakeLLM(fail_on="File: proj/b.py")
    first = run(setup, flaky)
    assert first.documented == ["proj/a.py"] and first.failed == ["proj/b.py"]
    healthy = FakeLLM()
    second = run(setup, healthy)
    assert second.ok and second.documented == ["proj/b.py"] and "proj/a.py" in second.skipped
    assert not [p for p in healthy.prompts if "File: proj/a.py" in p]


def test_force_regenerates_current_notes(setup, llm):
    repo, *_ = setup
    sha = repo.commit({"proj/b.py": B})
    run(setup, llm)
    from docdrift.core.git_utils import EMPTY_TREE
    assert run(setup, llm, from_sha=EMPTY_TREE, to_sha=sha).documented == []
    assert run(setup, llm, from_sha=EMPTY_TREE, to_sha=sha, force=True).documented == ["proj/b.py"]


def test_note_records_commit_and_source_hash(setup, llm):
    repo, vault, _ = setup
    sha = repo.commit({"proj/b.py": B})
    run(setup, llm)
    meta, _ = vault.read_note("Projects/proj/proj/b.md")
    assert meta["commit"] == sha[:12] and meta["source_hash"] == pipeline.source_hash(B)


def test_branch_switch_starts_at_the_merge_base(setup, llm):
    from conftest import git

    repo, *_ = setup
    repo.commit({"proj/a.py": A})
    git(repo.root, "checkout", "-q", "-b", "other")
    repo.commit({"proj/b.py": B}, "other branch")
    run(setup, llm)  # last_sha is now on "other"
    git(repo.root, "checkout", "-q", "-")
    repo.commit({"proj/c.py": "def c():\n    return 3\n"}, "main work")
    logs = []
    repo_, vault, cfg = setup
    result = pipeline.run_documentation(repo_.root, cfg, client=llm, vault=vault, log=logs.append)
    assert result.documented == ["proj/c.py"]  # b.py is neither re-documented nor "deleted"
    assert result.deleted == [] and any("another branch" in m for m in logs)


def test_lost_last_commit_falls_back_without_redoing_current_notes(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": B})
    run(setup, llm)
    state_dir = Path(repo.root) / ".docdrift" / "state"
    st = state.load_state(state_dir, "proj")
    st.last_sha = "0123456789abcdef0123456789abcdef01234567"  # gone after rebase + gc
    state.save_state(state_dir, "proj", st)
    repo.commit({"proj/c.py": "def c():\n    return 3\n"})
    result = run(setup, llm)
    assert result.ok and result.documented == ["proj/c.py"] and "proj/b.py" in result.skipped


def test_second_run_while_locked_is_refused(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": B})
    with state.run_lock(Path(repo.root) / ".docdrift" / "state"), pytest.raises(state.RunLocked):
        run(setup, llm)
    assert run(setup, llm).ok  # lock released


def test_empty_llm_reply_never_overwrites_a_note():
    with pytest.raises(ValueError):
        pipeline.parse_note("```markdown\n\n```", {})
    with pytest.raises(ValueError):
        pipeline.parse_note("---\nx: 1\n---\n", {})


def test_bom_file_is_analysed(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/b.py": "\ufeff" + B})
    result = run(setup, llm)
    assert result.documented == ["proj/b.py"]
    assert "could not be parsed" not in llm.prompts[0]


def test_unreachable_llm_stops_the_run_early(setup):
    from docdrift.local.llm_client import LLMUnavailable

    class Down:
        calls = 0

        def chat(self, messages, temperature=0.2):
            Down.calls += 1
            raise LLMUnavailable("Could not reach a local LLM server")

    repo, *_ = setup
    repo.commit({"proj/a.py": A, "proj/b.py": B, "proj/c.py": "def c():\n    return 3\n"})
    result = run(setup, Down())
    assert Down.calls == 1 and len(result.failed) == 3 and not result.architecture_updated


def test_missing_local_vault_is_an_error_not_created(tmp_path):
    from docdrift.local.vault import open_vault

    cfg = DocdriftConfig(
        repo_name="p", api_base_url="", model="", obsidian=ObsidianConfig("local", tmp_path / "typo"),
        github_docs=GithubDocsConfig(),
    )
    with pytest.raises(ValueError, match="does not exist"):
        open_vault(cfg)
    assert not (tmp_path / "typo").exists()


def test_overview_mode_writes_only_the_hla_note_with_docs_and_layout(setup, llm):
    repo, vault, cfg = setup
    cfg.mode = "overview"
    repo.commit({"proj/a.py": A, "proj/b.py": B, "README.md": "# Proj\n\nFeature: counts things.\n"})
    result = run(setup, llm)
    assert result.ok and result.documented == [] and result.architecture_updated
    package, overview = llm.prompts  # one call per package, then the project overview
    assert package.startswith("# Task: package") and "def work():" in package  # full source
    assert "Feature: counts things." in overview and "- proj/: 2 file(s)" in overview
    assert "Does the work." in overview  # the package summary feeds the overview
    assert not (vault.root / "Projects/proj/proj/a.md").exists()
    _, arch = vault.read_note("Projects/proj/_Architecture.md")
    assert "## Overview" in arch and "```mermaid" in arch
    assert "### `proj/`" in arch and "**How it works**" in arch  # heading demoted
    assert "- `b.py` - Worker.\n  `work`" in arch  # deterministic module list


def test_product_doc_change_regenerates_the_overview(setup, llm):
    repo, _, cfg = setup
    cfg.mode = "overview"
    repo.commit({"proj/a.py": A, "README.md": "# Proj\n"})
    run(setup, llm)
    repo.commit({"README.md": "# Proj\n\nNew feature.\n"})
    assert run(setup, llm).architecture_updated


def test_per_file_mode_skips_the_overview(setup, llm):
    repo, vault, cfg = setup
    cfg.mode = "per-file"
    repo.commit({"proj/a.py": A})
    result = run(setup, llm)
    assert result.documented == ["proj/a.py"] and not result.architecture_updated
    assert not (vault.root / "Projects/proj/_Architecture.md").exists()


def test_large_package_falls_back_to_its_outline(setup, llm):
    repo, _, cfg = setup
    cfg.mode = "overview"
    big = "def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(3000)) + "    return 1\n"
    repo.commit({"proj/big.py": big})
    cfg.max_prompt_chars = 4000
    run(setup, llm)
    package = next(p for p in llm.prompts if p.startswith("# Task: package"))
    assert "outline only" in package and "x2999" not in package and "def f()" in package


def test_mode_override_skipping_a_configured_stage_keeps_the_range_open(setup, llm):
    repo, vault, _ = setup
    repo.commit({"proj/a.py": A})
    result = run(setup, llm, mode="overview")
    assert result.documented == [] and result.architecture_updated
    assert state.load_state(Path(repo.root) / ".docdrift" / "state", "proj").last_sha is None
    result = run(setup, llm)  # config mode "both": the per-file notes follow
    assert result.documented == ["proj/a.py"] and not result.architecture_updated


def test_force_regenerates_the_overview_when_nothing_is_new(setup, llm):
    repo, *_ = setup
    repo.commit({"proj/a.py": A})
    run(setup, llm)
    calls = len(llm.prompts)
    result = run(setup, llm, mode="overview", force=True)
    assert result.architecture_updated and len(llm.prompts) == calls + 2
    assert llm.prompts[-1].startswith("# Task: architecture")


def test_unknown_mode_override_is_rejected(setup, llm):
    with pytest.raises(ValueError, match="Unknown mode"):
        run(setup, llm, mode="all")
