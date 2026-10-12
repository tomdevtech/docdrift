"""Tests for docdrift.merge (merge flow, product docs only) with a real Git repo; no LLM."""

import json

import pytest
from click.testing import CliRunner
from conftest import git

from docdrift import cli, merge
from docdrift.config import AdrConfig, DocdriftConfig, GithubDocsConfig, ObsidianConfig

CORE = '"""Core."""\ndef work():\n    return 1\n'
README = "# proj\n\nCall `work()` to do the work.\n"


def _cfg(docs=True, assignee="copilot", precheck=True):
    return DocdriftConfig(
        repo_name="proj", api_base_url="", model="", obsidian=ObsidianConfig("local"),
        github_docs=GithubDocsConfig(enabled=docs, assignee=assignee, precheck=precheck),
        ignore=["tests/"], adr=AdrConfig(threshold=1),  # any change would be "significant"
    )


@pytest.fixture
def merged(repo):
    """main with one commit, a feature branch with two commits; returns (repo, base, head)."""
    base = repo.commit({"proj/core.py": CORE, "README.md": README}, "init")
    git(repo.root, "checkout", "-q", "-b", "feature")
    repo.commit({"proj/api.py": "def serve(port):\n    return port\n"}, "feat!: add api module")
    head = repo.commit({"proj/core.py": CORE.replace("1", "2")}, "Use a plugin architecture")
    return repo, base, head


def _build(repo, cfg, base, head, **pr):
    defaults = {"number": 7, "title": "Add API", "body": "We need an HTTP API for X."}
    pull = merge.PullRequest(base_sha=base, head_sha=head, **{**defaults, **pr})
    return merge.build_copilot_task(repo.root, cfg, pull, target_sha=head, log=lambda m: None)


def test_user_facing_merge_asks_copilot_to_check_the_product_docs(merged):
    repo, base, head = merged
    task = _build(repo, _cfg(), base, head).task
    assert task is not None and task.assignees == [merge.COPILOT_LOGIN]
    assert task.title.startswith("docdrift: check product docs for #7")
    body = task.body
    assert body.startswith(merge.MARKER)
    assert "## Task - check and update the product documentation" in body
    assert "public module `proj/api.py` added" in body
    assert "`README.md:3` mentions `work` (implementation changed)" in body
    assert "do not modify any file" in body and "No documentation update needed" in body
    assert "> We need an HTTP API for X." in body and "added def serve(port)" in body


def test_strict_separation_no_technical_docs_for_copilot(merged):
    """Even a breaking, architectural change gives Copilot no ADR or architecture task."""
    repo, base, head = merged
    task = _build(repo, _cfg(), base, head).task
    assert "ADR" not in task.title
    assert "Architecture Decision Record" not in task.body.split("## Scope")[0]
    scope = task.body.split("## Scope - product documentation only")[1].split("## Facts")[0]
    assert "Do **not** write technical or internal documentation" in scope
    assert "`docs/adr/`" in scope and "outside this repository" in scope
    assert not hasattr(task, "adr_number")


def test_internal_bugfix_creates_no_task(repo):
    base = repo.commit({"proj/core.py": CORE, "README.md": "# proj\nA tool.\n"}, "init")
    head = repo.commit({"proj/core.py": CORE.replace("1", "3")}, "fix: off by one")
    result = _build(repo, _cfg(), base, head)
    assert result.task is None and "internal" in result.reason


def test_bugfix_in_documented_function_asks_for_a_check(repo):
    base = repo.commit({"proj/core.py": CORE, "README.md": README}, "init")
    head = repo.commit({"proj/core.py": CORE.replace("1", "3")}, "fix: off by one")
    task = _build(repo, _cfg(), base, head).task
    assert task is not None
    assert [r for r in task.docs_check.reasons if "mentions changed code" in r]


def test_precheck_off_always_asks(repo):
    base = repo.commit({"proj/core.py": CORE}, "init")
    head = repo.commit({"proj/core.py": CORE.replace("1", "3")}, "fix: x")
    task = _build(repo, _cfg(precheck=False), base, head).task
    assert task is not None and "pre-check disabled" in task.body


def test_disabled_product_docs_do_nothing(merged):
    repo, base, head = merged
    result = _build(repo, _cfg(docs=False), base, head)
    assert result.task is None and "enabled is false" in result.reason


def test_copilot_branches_are_skipped(merged):
    repo, base, head = merged
    result = _build(repo, _cfg(), base, head, head_ref="copilot/fix-docs")
    assert result.task is None and "Copilot" in result.reason


def test_documentation_only_merge_is_skipped(repo):
    """Copilot's docs PR must not trigger the next Copilot task (loop guard)."""
    base = repo.commit({"proj/core.py": CORE}, "init")
    head = repo.commit(
        {"README.md": "# new\n", "docs/guide.md": "x\n", "docs/adr/0001-x.md": "# ADR 0001: X\n",
         "tests/test_x.py": "def test(): pass\n"},
        "docs: update",
    )
    result = _build(repo, _cfg(), base, head)
    assert result.task is None and "documentation" in result.reason


def test_technical_docs_in_the_repo_are_not_in_copilots_scope(repo):
    base = repo.commit(
        {"proj/core.py": CORE, "README.md": README, "docs/adr/0001-x.md": "Uses `work()` internally.\n"},
        "init",
    )
    head = repo.commit({"proj/core.py": CORE.replace("def work():", "def work(x):")}, "feat: x")
    task = _build(repo, _cfg(), base, head).task
    assert task.docs_check.doc_files == ["README.md"]
    assert all(not m.path.startswith("docs/adr/") for m in task.docs_check.mentions)


def test_range_is_exact_when_main_moved_on(merged):
    """Changes merged to main by other PRs must not show up in this PR's task."""
    repo, _, head = merged
    git(repo.root, "checkout", "-q", "-")
    other = repo.commit({"proj/other.py": "def other():\n    return 0\n"}, "feat: other PR")
    task = _build(repo, _cfg(), other, head).task  # base.sha = newer main
    assert "proj/other.py" not in task.body and "proj/api.py" in task.body


def test_assignee_resolution_and_issue_payload():
    assert merge.resolve_assignees("copilot") == [merge.COPILOT_LOGIN]
    assert merge.resolve_assignees("@Copilot") == [merge.COPILOT_LOGIN]
    assert merge.resolve_assignees("octocat") == ["octocat"]
    assert merge.resolve_assignees("") == []
    assert "assignees" not in merge.CopilotTask("t", "b").to_issue()


def test_long_pr_body_and_big_changes_stay_below_github_limit(repo):
    base = repo.commit({"proj/core.py": CORE}, "init")
    files = {f"proj/m{i}.py": "".join(f"def f{j}(a, b):\n    return a\n" for j in range(80)) for i in range(70)}
    head = repo.commit(files, "feat: many modules")
    task = _build(repo, _cfg(), base, head, body="x" * 100_000).task
    assert len(task.body) <= 60_000 and "... and" in task.body


def test_cli_writes_issue_json_and_removes_stale_output(merged, monkeypatch, tmp_path):
    repo, base, head = merged
    (repo.root / ".docdrift").mkdir()
    (repo.root / ".docdrift" / "config.yaml").write_text("ignore:\n  - tests/\n", encoding="utf-8")
    monkeypatch.chdir(repo.root)
    body = tmp_path / "body.md"
    body.write_text("Why: speed.", encoding="utf-8")
    out = tmp_path / "issue.json"
    args = ["copilot-task", "--pr", "3", "--base", base, "--head", head, "--title", "Add API",
            "--body-file", str(body), "--output", str(out)]

    result = CliRunner().invoke(cli.main, args)
    assert result.exit_code == 0, result.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["assignees"] == [merge.COPILOT_LOGIN] and "> Why: speed." in payload["body"]

    result = CliRunner().invoke(cli.main, [*args[:-2], "--head-ref", "copilot/x", "--output", str(out)])
    assert result.exit_code == 0 and "Nothing to do" in result.output and not out.exists()
