"""Tests for docdrift.merge.models (engine ``github-models``) with a fake GitHub Models client."""

import json

import pytest
from click.testing import CliRunner
from conftest import git

from docdrift.cli import commands as cli
from docdrift.core.config import (
    AdrConfig,
    DocdriftConfig,
    GithubDocsConfig,
    ObsidianConfig,
    load_config,
)
from docdrift.merge import models
from docdrift.merge import task as merge
from docdrift.merge.docs_check import DocMention

CORE = '"""Core."""\ndef work():\n    return 1\n'
README = "# proj\n\nCall `work()` to do the work.\n\n## Usage\n\nRun it.\n"


class FakeModels:
    """Answers every file with the given edits and records the prompts."""

    def __init__(self, edits=None, reason="Documented the new option."):
        self.edits = edits if edits is not None else [{"find": "Call `work()`", "replace": "Call `work(n)`"}]
        self.reason = reason
        self.prompts = []

    def chat(self, system, user):
        self.prompts.append(user)
        return json.dumps({"edits": self.edits, "reason": self.reason})


def _cfg(**gd):
    return DocdriftConfig(
        repo_name="proj", api_base_url="", model="", obsidian=ObsidianConfig("local"),
        github_docs=GithubDocsConfig(engine="github-models", **gd), ignore=["tests/"],
        adr=AdrConfig(threshold=1),
    )


@pytest.fixture
def merged(repo):
    base = repo.commit({"proj/core.py": CORE, "README.md": README}, "init")
    git(repo.root, "checkout", "-q", "-b", "feature")
    head = repo.commit({"proj/core.py": CORE.replace("work()", "work(n)")}, "feat: work takes n")
    return repo, base, head


def _task(repo, cfg, base, head, **pr):
    pull = merge.PullRequest(number=7, base_sha=base, head_sha=head, title="work(n)", **pr)
    return merge.build_copilot_task(repo.root, cfg, pull, target_sha=head, log=lambda m: None), pull


def test_parse_reply_tolerates_a_fence_and_rejects_non_json():
    edits, reason = models.parse_reply('```json\n{"edits": [{"find": "a", "replace": "b"}], "reason": "r"}\n```')
    assert edits == [models.Edit("a", "b")] and reason == "r"
    assert models.parse_reply('{"edits": [], "reason": "fine"}') == ([], "fine")
    with pytest.raises(models.ModelsError):
        models.parse_reply("Sure! Here are the edits:")


def test_only_unique_finds_are_applied():
    text = "a\nb\nb\n"
    new, applied, rejected = models.apply_edits(
        text, [models.Edit("a", "A"), models.Edit("b", "B"), models.Edit("zzz", "x")]
    )
    assert new == "A\nb\nb\n" and applied == 1 and rejected == ["b", "zzz"]


def test_docs_are_edited_in_place_and_summarised(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    fake = FakeModels()
    outcome = models.update_docs(repo.root, _cfg(), result.task, pr, client=fake, log=lambda m: None)

    assert outcome.changed and outcome.files[0].path == "README.md" and outcome.files[0].applied == 1
    assert "Call `work(n)`" in (repo.root / "README.md").read_text(encoding="utf-8")
    prompt = fake.prompts[0]
    assert "README.md:3" in prompt and "work" in prompt and "## Usage" in prompt  # mention + file
    summary = outcome.summary(pr, "openai/gpt-4.1-mini")
    assert merge.MARKER in summary and "#7" in summary and "1 edit(s)" in summary


def test_no_edits_means_no_update_needed(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    outcome = models.update_docs(
        repo.root, _cfg(), result.task, pr, client=FakeModels(edits=[], reason="Still correct."), log=lambda m: None
    )
    assert not outcome.changed
    assert "No documentation update needed" in outcome.summary(pr, "m")
    assert (repo.root / "README.md").read_text(encoding="utf-8") == README


def test_file_too_large_for_the_budget_is_skipped(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    fake = FakeModels()
    outcome = models.update_docs(repo.root, _cfg(models_max_chars=300), result.task, pr, client=fake, log=lambda m: None)
    assert outcome.files[0].skipped and not fake.prompts and not outcome.changed


def test_files_with_mentions_come_first_and_readme_is_always_checked(merged):
    repo, base, head = merged
    result, _ = _task(repo, _cfg(), base, head)
    task = result.task
    task.docs_check.doc_files = ["README.md", "docs/a.md", "docs/b.md"]
    task.docs_check.mentions = [
        DocMention("docs/b.md", 1, "work", "changed", "x"),
        DocMention("docs/b.md", 2, "work", "changed", "x"),
        DocMention("docs/a.md", 1, "work", "changed", "x"),
    ]
    assert models.select_files(task, 3) == ["docs/b.md", "docs/a.md", "README.md"]
    assert models.select_files(task, 1) == ["docs/b.md"]


def test_client_reports_http_errors(monkeypatch):
    class Response:
        ok, status_code, text = False, 429, "rate limit"

    monkeypatch.setattr(models.requests, "post", lambda *a, **k: Response())
    with pytest.raises(models.ModelsError, match="429"):
        models.ModelsClient("t", "m").chat("s", "u")


def test_docdrift_branches_are_skipped(merged):
    repo, base, head = merged
    result, _ = _task(repo, _cfg(), base, head, head_ref="docdrift/docs-pr-7")
    assert result.task is None and "docdrift docs update" in result.reason


def test_engine_config_default_and_validation(tmp_path):
    (tmp_path / ".docdrift").mkdir()
    cfg_file = tmp_path / ".docdrift" / "config.yaml"
    cfg_file.write_text('obsidian:\n  access_mode: "local"\n', encoding="utf-8")
    assert load_config(tmp_path).github_docs.engine == "copilot"
    cfg_file.write_text('github_docs:\n  engine: "GitHub-Models"\n', encoding="utf-8")
    assert load_config(tmp_path).github_docs.engine == "github-models"
    cfg_file.write_text('github_docs:\n  engine: "claude"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="engine"):
        load_config(tmp_path)


def test_cli_models_docs_edits_and_sets_step_outputs(merged, monkeypatch, tmp_path):
    repo, base, head = merged
    (repo.root / ".docdrift").mkdir()
    (repo.root / ".docdrift" / "config.yaml").write_text(
        'github_docs:\n  engine: "github-models"\nignore:\n  - tests/\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo.root)
    monkeypatch.setattr(models, "ModelsClient", lambda token, model: FakeModels())
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    summary = tmp_path / "summary.md"
    pr_args = ["--pr", "7", "--base", base, "--head", head, "--title", "work(n)"]

    result = CliRunner().invoke(cli.main, ["copilot-task", *pr_args, "--output", str(tmp_path / "i.json")])
    assert result.exit_code == 0, result.output
    assert "engine=github-models" in outputs.read_text(encoding="utf-8")

    result = CliRunner().invoke(cli.main, ["models-docs", *pr_args, "--summary", str(summary)])
    assert result.exit_code == 0, result.output
    assert "changed=true" in outputs.read_text(encoding="utf-8") and "1 edit(s)" in summary.read_text(encoding="utf-8")
    assert "Call `work(n)`" in (repo.root / "README.md").read_text(encoding="utf-8")

    monkeypatch.delenv("GITHUB_TOKEN")
    result = CliRunner().invoke(cli.main, ["models-docs", *pr_args, "--summary", str(summary)])
    assert result.exit_code != 0 and "GITHUB_TOKEN" in result.output
