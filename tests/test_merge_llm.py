"""Tests for docdrift.merge.llm (engine ``llm``) with a fake OpenAI-compatible client."""

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
from docdrift.merge import llm
from docdrift.merge import task as merge
from docdrift.merge.docs_check import DocMention

CORE = '"""Core."""\ndef work():\n    return 1\n'
README = "# proj\n\nCall `work()` to do the work.\n\n## Usage\n\nRun it.\n"


class FakeLLM:
    """Answers every file with the given edits and records the prompts."""

    def __init__(self, edits=None, reason="Documented the new option."):
        self.edits = edits if edits is not None else [{"find": "Call `work()`", "replace": "Call `work(n)`"}]
        self.reason = reason
        self.prompts = []

    def chat(self, system, user, history=None):
        self.prompts.append(user)
        return json.dumps({"edits": self.edits, "reason": self.reason})


class ChattyLLM:
    """Thinks out loud instead of answering in JSON, unless asked again (``obeys_retry``)."""

    def __init__(self, obeys_retry):
        self.obeys_retry = obeys_retry
        self.calls = []

    def chat(self, system, user, history=None):
        self.calls.append(history)
        if history and self.obeys_retry:
            return '{"edits": [{"find": "Call `work()`", "replace": "Call `work(n)`"}], "reason": "r"}'
        return "We need to decide if README.md is still correct. Key points: ..."


def _cfg(**gd):
    return DocdriftConfig(
        repo_name="proj", api_base_url="", model="", obsidian=ObsidianConfig("local"),
        github_docs=GithubDocsConfig(engine="llm", **gd), ignore=["tests/"],
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


def test_parse_reply_tolerates_fences_reasoning_and_chatter():
    edits, reason = llm.parse_reply('```json\n{"edits": [{"find": "a", "replace": "b"}], "reason": "r"}\n```')
    assert edits == [llm.Edit("a", "b")] and reason == "r"
    assert llm.parse_reply('{"edits": [], "reason": "fine"}') == ([], "fine")
    # Free models often think out loud or add a sentence around the JSON.
    noisy = '<think>The file mentions {work}.</think>Here you go:\n{"edits": [], "reason": "ok"}\nHope it helps.'
    assert llm.parse_reply(noisy) == ([], "ok")
    with pytest.raises(llm.LLMError):
        llm.parse_reply("Sure! Here are the edits:")


def test_only_unique_finds_are_applied():
    text = "a\nb\nb\n"
    new, applied, rejected = llm.apply_edits(
        text, [llm.Edit("a", "A"), llm.Edit("b", "B"), llm.Edit("zzz", "x")]
    )
    assert new == "A\nb\nb\n" and applied == 1 and rejected == ["b", "zzz"]


def test_docs_are_edited_in_place_and_summarised(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    fake = FakeLLM()
    outcome = llm.update_docs(repo.root, _cfg(), result.task, pr, client=fake, log=lambda m: None)

    assert outcome.changed and outcome.files[0].path == "README.md" and outcome.files[0].applied == 1
    assert "Call `work(n)`" in (repo.root / "README.md").read_text(encoding="utf-8")
    prompt = fake.prompts[0]
    assert "README.md:3" in prompt and "work" in prompt and "## Usage" in prompt  # mention + file
    assert "return 1" not in prompt  # source code is never sent, only AST facts and the doc
    summary = outcome.summary(pr, "openrouter/free")
    assert merge.MARKER in summary and "#7" in summary and "1 edit(s)" in summary


def test_no_edits_means_no_update_needed(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    outcome = llm.update_docs(
        repo.root, _cfg(), result.task, pr, client=FakeLLM(edits=[], reason="Still correct."), log=lambda m: None
    )
    assert not outcome.changed
    assert "No documentation update needed" in outcome.summary(pr, "m")
    assert (repo.root / "README.md").read_text(encoding="utf-8") == README


def test_a_reply_without_json_is_asked_again(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    chatty = ChattyLLM(obeys_retry=True)
    outcome = llm.update_docs(repo.root, _cfg(), result.task, pr, client=chatty, log=lambda m: None)
    assert outcome.changed and len(chatty.calls) == 2
    assert chatty.calls[1][0][0] == "assistant" and "ONLY the JSON" in chatty.calls[1][1][1]


def test_unusable_answers_skip_the_file_and_fail_only_if_nothing_was_answered(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    with pytest.raises(llm.LLMError, match="no doc file got a usable answer"):
        llm.update_docs(repo.root, _cfg(), result.task, pr, client=ChattyLLM(obeys_retry=False), log=lambda m: None)

    # Two files: the first answer is unusable, the second fine -> the run goes on.
    (repo.root / "docs").mkdir()
    (repo.root / "docs" / "guide.md").write_text("Use `work()`.\n", encoding="utf-8")
    result.task.docs_check.doc_files = ["README.md", "docs/guide.md"]
    result.task.docs_check.mentions = [DocMention("docs/guide.md", 1, "work", "changed", "x")]

    class FirstChatty(ChattyLLM):
        def chat(self, system, user, history=None):
            if "File `docs/guide.md`" in user:
                return "I think the guide is fine."
            return FakeLLM().chat(system, user)

    outcome = llm.update_docs(repo.root, _cfg(), result.task, pr, client=FirstChatty(False), log=lambda m: None)
    guide, readme = outcome.files
    assert guide.skipped.startswith("the model gave no usable answer") and readme.applied == 1
    assert "skipped - the model gave no usable answer" in outcome.summary(pr, "m")


def test_file_too_large_for_the_budget_is_skipped(merged):
    repo, base, head = merged
    result, pr = _task(repo, _cfg(), base, head)
    fake = FakeLLM()
    outcome = llm.update_docs(repo.root, _cfg(llm_max_chars=300), result.task, pr, client=fake, log=lambda m: None)
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
    assert llm.select_files(task, 3) == ["docs/b.md", "docs/a.md", "README.md"]
    assert llm.select_files(task, 1) == ["docs/b.md"]


class _Response:
    def __init__(self, ok=True, status_code=200, text="", data=None):
        self.ok, self.status_code, self.text, self._data = ok, status_code, text, data

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def test_client_sends_key_and_reports_errors(monkeypatch):
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, body=json)
        return _Response(data={"choices": [{"message": {"content": "hi"}}]})

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.LLMClient("https://openrouter.ai/api/v1/", "openrouter/free", api_key="k").chat("s", "u") == "hi"
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer k" and seen["body"]["model"] == "openrouter/free"

    assert seen["body"]["response_format"] == {"type": "json_object"} and seen["body"]["max_tokens"]

    llm.LLMClient("http://localhost:1234/v1", "m").chat("s", "u", history=[("assistant", "a"), ("user", "b")])
    assert "Authorization" not in seen["headers"]  # a local server needs no key
    assert "response_format" not in seen["body"]  # JSON mode only where it is known to work
    assert [m["role"] for m in seen["body"]["messages"]] == ["system", "user", "assistant", "user"]

    cases = [
        (_Response(ok=False, status_code=429, text="rate limit"), "429"),
        (_Response(text="OK"), "did not answer with JSON"),  # e.g. a retired endpoint
        (_Response(data={"error": {"message": "no endpoints"}}), "no endpoints"),
    ]
    for response, message in cases:
        monkeypatch.setattr(llm.requests, "post", lambda *a, response=response, **k: response)
        with pytest.raises(llm.LLMError, match=message):
            llm.LLMClient("https://x/v1", "m", api_key="k").chat("s", "u")


def test_docdrift_branches_are_skipped(merged):
    repo, base, head = merged
    result, _ = _task(repo, _cfg(), base, head, head_ref="docdrift/docs-pr-7")
    assert result.task is None and "docdrift docs update" in result.reason


def test_engine_config_default_validation_and_retired_github_models(tmp_path):
    (tmp_path / ".docdrift").mkdir()
    cfg_file = tmp_path / ".docdrift" / "config.yaml"
    cfg_file.write_text('obsidian:\n  access_mode: "local"\n', encoding="utf-8")
    gd = load_config(tmp_path).github_docs
    assert gd.engine == "copilot" and gd.llm_api_base_url == "https://openrouter.ai/api/v1"
    assert gd.llm_model == "openrouter/free"
    cfg_file.write_text('github_docs:\n  engine: "LLM"\n  llm_model: "x:free"\n', encoding="utf-8")
    gd = load_config(tmp_path).github_docs
    assert gd.engine == "llm" and gd.llm_model == "x:free"
    cfg_file.write_text('github_docs:\n  engine: "github-models"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="retired"):
        load_config(tmp_path)
    cfg_file.write_text('github_docs:\n  engine: "claude"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="engine"):
        load_config(tmp_path)


def test_cli_llm_docs_edits_and_sets_step_outputs(merged, monkeypatch, tmp_path):
    repo, base, head = merged
    (repo.root / ".docdrift").mkdir()
    (repo.root / ".docdrift" / "config.yaml").write_text(
        'github_docs:\n  engine: "llm"\nignore:\n  - tests/\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo.root)
    monkeypatch.setattr(llm, "LLMClient", lambda base_url, model, api_key="": FakeLLM())
    outputs = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))
    monkeypatch.setenv("DOCDRIFT_LLM_API_KEY", "k")
    summary = tmp_path / "summary.md"
    pr_args = ["--pr", "7", "--base", base, "--head", head, "--title", "work(n)"]

    result = CliRunner().invoke(cli.main, ["copilot-task", *pr_args, "--output", str(tmp_path / "i.json")])
    assert result.exit_code == 0, result.output
    assert "engine=llm" in outputs.read_text(encoding="utf-8")

    result = CliRunner().invoke(cli.main, ["llm-docs", *pr_args, "--summary", str(summary)])
    assert result.exit_code == 0, result.output
    assert "changed=true" in outputs.read_text(encoding="utf-8") and "1 edit(s)" in summary.read_text(encoding="utf-8")
    assert "Call `work(n)`" in (repo.root / "README.md").read_text(encoding="utf-8")

    monkeypatch.delenv("DOCDRIFT_LLM_API_KEY")
    result = CliRunner().invoke(cli.main, ["llm-docs", *pr_args, "--summary", str(summary)])
    assert result.exit_code != 0 and "DOCDRIFT_LLM_API_KEY" in result.output
