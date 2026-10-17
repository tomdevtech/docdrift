"""Merge flow: keep the product docs up to date after a pull request is merged.

Runs in GitHub Actions after a merge into ``main`` (``docdrift copilot-task``,
installed by ``docdrift init-github-product``). No LLM runs on the runner:

1. **Range** - exactly the pull request's changes (merge base to PR head).
2. **Pre-check** (:mod:`docdrift.merge.docs_check`) - could the change affect users at
   all? Internal changes (bug fixes inside functions, private code, tests,
   formatting) stop here, so they cost no Copilot request.
3. **Issue** - otherwise the Copilot coding agent gets an issue: first *check*
   whether README / ``docs_paths`` are still correct, then update only what is
   outdated. The issue carries the reasons of the pre-check and the exact lines
   of the docs that mention changed code. If nothing needs to change, Copilot
   changes no file and says so in its pull request, which you then close.

The task is executed by the configured ``github_docs.engine``: the Copilot
coding agent (this issue) or GitHub Models (:mod:`docdrift.merge.models`, which
uses the same task and opens the pull request itself).

Strict separation: Copilot writes **product docs only** - what users of the
project read. Everything technical (per-file notes, architecture, ADRs) is the
local flow's job and lives in the Obsidian vault. The issue says so explicitly,
and technical docs in the repository (``docs/adr/``) are out of Copilot's scope.

Loops are prevented twice: the docs pull requests themselves (``copilot/*`` and
``docdrift/*`` branches) are skipped, and documentation files never count as a
reason for a task.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from docdrift.core import git_utils
from docdrift.core.changes import Log, Snapshot, collect_changes
from docdrift.core.config import DocdriftConfig
from docdrift.merge.docs_check import TECHNICAL_DOC_DIRS, DocsCheck, check_docs, is_doc_file

#: Login of the Copilot coding agent for the REST API (``assignees``).
COPILOT_LOGIN = "copilot-swe-agent[bot]"
#: Branch prefix of pull requests opened by the Copilot coding agent.
COPILOT_BRANCH_PREFIX = "copilot/"
#: Branch prefix of the docs pull requests opened by the ``github-models`` engine.
DOCS_BRANCH_PREFIX = "docdrift/"
#: Hidden marker in every generated issue (lets people and scripts find them).
MARKER = "<!-- docdrift:merge-task -->"

_MAX_BODY = 60_000  # GitHub's issue body limit is 65 536 characters
_FENCE = "````"  # four backticks: survives triple backticks inside the content


@dataclass
class PullRequest:
    """The merged pull request, as GitHub Actions reports it."""

    number: int
    base_sha: str
    head_sha: str
    title: str = ""
    body: str = ""
    head_ref: str = ""


@dataclass
class CopilotTask:
    """An issue for the Copilot coding agent."""

    title: str
    body: str
    assignees: list[str] = field(default_factory=list)
    docs_check: DocsCheck = field(default_factory=DocsCheck)
    #: AST changes of the merge (used by the ``github-models`` engine).
    structure: str = ""

    def to_issue(self) -> dict:
        """Payload for ``POST /repos/{owner}/{repo}/issues``."""
        payload = {"title": self.title, "body": self.body}
        if self.assignees:
            payload["assignees"] = self.assignees
        return payload


@dataclass
class TaskResult:
    """Outcome of :func:`build_copilot_task`: a task, or the reason for none."""

    task: CopilotTask | None
    reason: str


# --- helpers --------------------------------------------------------------------


def resolve_assignees(assignee: str) -> list[str]:
    """``"copilot"`` -> the coding agent's login; empty -> nobody; else the login itself."""
    value = (assignee or "").strip()
    if not value:
        return []
    if value.lower().lstrip("@") == "copilot":
        return [COPILOT_LOGIN]
    return [value]


def _docs_patterns(cfg: DocdriftConfig) -> list[str]:
    """Paths that are documentation themselves (never a reason for a task)."""
    return [*cfg.github_docs.docs_paths, *TECHNICAL_DOC_DIRS, ".github/"]


def _read_docs(repo_root: Path, cfg: DocdriftConfig, sha: str) -> dict[str, str]:
    """Product documentation files at ``sha``: path -> text."""
    docs = {}
    for path in git_utils.list_files(repo_root, sha):
        if is_doc_file(path, cfg.github_docs.docs_paths):
            text = git_utils.read_file(repo_root, path, sha)
            if text is not None and "\0" not in text:
                docs[path.as_posix()] = text
    return docs


def _quote(text: str, limit: int) -> str:
    text = text.strip()
    if not text:
        return "> (no description)"
    if len(text) > limit:
        text = text[:limit] + "\n... (truncated)"
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines())


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


# --- rendering -------------------------------------------------------------------


def _docs_section(cfg: DocdriftConfig, check: DocsCheck) -> str:
    scope = ", ".join(f"`{p}`" for p in cfg.github_docs.docs_paths) or "`README.md`"
    files = "\n".join(f"- `{f}`" for f in check.doc_files) or "- (none yet)"
    reasons = "\n".join(f"- {r}" for r in check.reasons) or (
        "- (pre-check disabled - check the changes below)"
    )
    mentions = "\n".join(m.render() for m in check.mentions)
    return (
        "## Task - check and update the product documentation\n\n"
        "**First decide whether the documentation is still correct for users**, then "
        "change only what this merge made wrong or incomplete.\n\n"
        "docdrift's pre-check found these hints. They say *what might* be affected, not "
        "that something *is* wrong:\n\n"
        f"{reasons}\n\n"
        + (
            f"Start here - these lines mention code that changed:\n\n{mentions}\n\n"
            if mentions else ""
        )
        + f"Product documentation in scope ({scope}):\n\n{files}\n\n"
        "How to work:\n\n"
        "1. For every hint, compare the documentation with the merged code: commands, "
        "options, configuration keys and defaults, public API, examples, installation, "
        "behaviour users can observe.\n"
        "2. Update what is outdated, and document new user-facing features where readers "
        "would look for them. Keep structure, tone and language of the existing files; "
        "no rewrites, no implementation details, no changelog-style notes.\n"
        "3. Code examples in the docs must match the merged signatures exactly.\n"
        "4. **If nothing needs to change, do not modify any file.** Write \"No "
        "documentation update needed\" in the pull request description, with one short "
        "sentence per hint above explaining why, so the pull request can be closed."
    )


def _render_body(
    *,
    cfg: DocdriftConfig,
    pr: PullRequest,
    from_sha: str,
    snap: Snapshot,
    messages: list[str],
    check: DocsCheck,
) -> str:
    parts = [
        MARKER,
        (
            f"Pull request #{pr.number} (**{_shorten(pr.title, 120) or 'untitled'}**) was "
            "merged into `main`. Please check the product documentation and update it in "
            "**one** pull request if needed."
        ),
        (
            "Generated by docdrift. The facts at the end are computed from git and the "
            "Python AST, not guessed."
        ),
    ]
    parts.append(_docs_section(cfg, check))
    technical = ", ".join(f"`{d}`" for d in TECHNICAL_DOC_DIRS)
    parts.append(
        "## Scope - product documentation only\n\n"
        "- Write for **users** of the project: what it does, how to install, configure "
        "and use it.\n"
        "- Do **not** write technical or internal documentation: no architecture "
        "descriptions, module internals, design rationale or Architecture Decision "
        f"Records. Do not create or edit files in {technical}. Technical documentation "
        "is maintained separately, outside this repository.\n"
        "- Do not change source code, tests, configuration or workflow files.\n"
        f"- Mention #{pr.number} in the pull request description."
    )

    files = [f"- `{p.as_posix()}` (added)" for p in snap.changes.added]
    files += [f"- `{p.as_posix()}` (modified)" for p in snap.changes.modified]
    files += [f"- `{p.as_posix()}` (deleted)" for p in snap.changes.deleted]
    if len(files) > 60:
        files = files[:60] + [f"- ... and {len(files) - 60} more"]
    commit_text = "\n---\n".join(messages)
    parts.append(
        "## Facts\n\n"
        f"Range: `{from_sha[:8]}..{pr.head_sha[:8]}` (merge base to pull request head)\n\n"
        f"### Pull request description\n\n{_quote(pr.body, 4000)}\n\n"
        f"### Commit messages\n\n{_FENCE}text\n{commit_text[:4000]}\n{_FENCE}\n\n"
        "### Changed files (documentation and ignored files left out)\n\n" + "\n".join(files)
    )

    body = "\n\n".join(parts)
    budget = _MAX_BODY - len(body) - 200
    structure = snap.changeset.render_all()
    if budget > 1000:
        if len(structure) > budget:
            structure = structure[:budget] + "\n... (truncated)"
        body += (
            "\n\n### Structural changes (computed from the AST)\n\n"
            f"{_FENCE}text\n{structure}\n{_FENCE}\n"
        )
    return body[:_MAX_BODY]


# --- public API ------------------------------------------------------------------


def build_copilot_task(
    repo_root: Path | str,
    cfg: DocdriftConfig,
    pr: PullRequest,
    *,
    target_sha: str = "HEAD",
    log: Log = print,
) -> TaskResult:
    """Decide whether Copilot has to look at the product docs after a merge; write the issue.

    Args:
        repo_root: checkout of the repository with full history.
        cfg: configuration of the repository.
        pr: the merged pull request (``base_sha`` and ``head_sha`` from the event).
        target_sha: the merge result on ``main`` (the current docs are read there).
        log: progress callback.

    Returns:
        a :class:`TaskResult`; ``task`` is ``None`` when there is nothing to do,
        and ``reason`` explains why.
    """
    repo_root = Path(repo_root)

    if pr.head_ref.startswith(COPILOT_BRANCH_PREFIX):
        return TaskResult(None, "pull request was opened by the Copilot coding agent")
    if pr.head_ref.startswith(DOCS_BRANCH_PREFIX):
        return TaskResult(None, "pull request is a docdrift docs update")
    if not cfg.github_docs.enabled:
        return TaskResult(None, "github_docs.enabled is false")

    from_sha = git_utils.merge_base(repo_root, pr.base_sha, pr.head_sha)
    snap = collect_changes(repo_root, cfg, from_sha, pr.head_sha, extra_ignore=_docs_patterns(cfg))
    if snap is None:
        return TaskResult(None, "only documentation or ignored files changed")

    commit_messages = git_utils.get_commit_messages(repo_root, from_sha, pr.head_sha)
    messages = [m for m in [pr.title.strip(), *commit_messages] if m]

    check = check_docs(snap.changeset, snap.changes, messages, _read_docs(repo_root, cfg, target_sha))
    for reason in check.reasons:
        log(f"Docs hint: {reason}")
    if not check.needed and cfg.github_docs.precheck:
        return TaskResult(None, "only internal changes - nothing users could notice")

    body = _render_body(cfg=cfg, pr=pr, from_sha=from_sha, snap=snap, messages=messages, check=check)
    title = f"docdrift: check product docs for #{pr.number} - {_shorten(pr.title, 120) or 'merged PR'}"
    task = CopilotTask(
        title=title,
        body=body,
        assignees=resolve_assignees(cfg.github_docs.assignee),
        docs_check=check,
        structure=snap.changeset.render_all(),
    )
    return TaskResult(task, "task created")


__all__ = [
    "COPILOT_LOGIN",
    "DOCS_BRANCH_PREFIX",
    "CopilotTask",
    "PullRequest",
    "TaskResult",
    "build_copilot_task",
    "resolve_assignees",
]
