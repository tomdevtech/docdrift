"""Shared fixtures: a throw-away Git repository and a fake LLM."""

import subprocess
from pathlib import Path

import pytest

from docdrift.llm_client import ChatMessage


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.strip()


class Repo:
    """Minimal helper around a temporary Git repository."""

    def __init__(self, root: Path) -> None:
        self.root = root
        git(root, "init", "-q")
        git(root, "config", "user.email", "t@example.com")
        git(root, "config", "user.name", "Test")
        git(root, "config", "commit.gpgsign", "false")

    def commit(self, files: dict[str, str | None], message: str = "change") -> str:
        """Write (or delete, if the value is None) files and commit; return the SHA."""
        for name, content in files.items():
            path = self.root / name
            if content is None:
                path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)
        return git(self.root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    root = tmp_path / "proj"
    root.mkdir()
    return Repo(root)


class FakeLLM:
    """Answers per task type (taken from the prompt header) and records all prompts."""

    def __init__(self, fail_on: str | None = None) -> None:
        self.prompts: list[str] = []
        self.fail_on = fail_on

    def chat(self, messages: list[ChatMessage], temperature: float = 0.2) -> str:
        user = messages[-1].content
        self.prompts.append(user)
        if self.fail_on and self.fail_on in user:
            raise RuntimeError("LLM down")
        if user.startswith("# Task: architecture"):
            return "## Overview\nA small tool.\n\n## Components\n- [[a]] - does things"
        if user.startswith("# Task: ADR"):
            return (
                "Title: Split the tool into modules\n\n## Context\nIt grew.\n\n"
                "## Decision\nNew modules.\n\n## Consequences\n- more files"
            )
        return "```markdown\n## Purpose\nDocumented.\n\n## Public interface\n-\n```"


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
