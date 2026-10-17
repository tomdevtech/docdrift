"""Merge flow engine ``llm``: update the product docs with any OpenAI-compatible LLM.

The alternative to the Copilot coding agent for accounts without a Copilot
licence (``github_docs.engine: llm``). It works with every OpenAI-compatible
chat completions API: OpenRouter (default, free models), Groq, Google Gemini,
Mistral, or a local LM Studio / Ollama on a self-hosted runner. The workflow
opens the pull request itself.

Input is the same task as for Copilot (:func:`docdrift.merge.task.build_copilot_task`):
the pre-check's hints, the doc lines that mention changed code and the AST
changes. Source code is never sent, only the doc files themselves.

Free tiers are small, so docdrift asks for one doc file at a time (at most
``llm_max_files``, files with mentions first, then ``README.md``) and the model
answers with small find/replace edits instead of whole files. An edit is
applied only if its ``find`` text occurs exactly once in the file - the model can
never rewrite, truncate or create files, and it only sees files docdrift chose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import requests

from docdrift.core.changes import Log
from docdrift.core.config import DocdriftConfig
from docdrift.merge.task import MARKER, CopilotTask, PullRequest

SYSTEM_PROMPT = """\
You keep ONE product documentation file of a software project up to date after a
pull request was merged. Product documentation is what users read: what the
project does and how to install, configure and use it.

You get hints from an automatic pre-check (what *might* be affected), the lines
of this file that mention changed code, the structural code changes (computed
from the AST) and the current file.

First decide whether this file is still correct and complete for users. Then
return ONLY a JSON object, nothing before or after it, no code fence:

{"edits": [{"find": "...", "replace": "..."}], "reason": "..."}

Rules:
- `find` is copied exactly from the file (whitespace included) and long enough to
  occur only once - a full line or paragraph. `replace` is its new text.
- To add text, use an existing line as `find` and that line plus the new text
  as `replace`.
- Change only what the merge made wrong or incomplete; document new user-facing
  features where readers would look for them. Keep structure, tone and language.
- Code examples must match the changed signatures exactly.
- Never write technical documentation: no architecture, module internals, design
  rationale or decision records.
- If nothing needs to change, return {"edits": [], "reason": "<why, one sentence per hint>"}.
- `reason` is one or two sentences for the reviewer.
"""

_THINK = re.compile(r"<(think|thinking|reasoning)>.*?</\1>", re.DOTALL | re.IGNORECASE)


class LLMError(RuntimeError):
    """The LLM could not be reached or answered with something unusable."""


@dataclass
class Edit:
    find: str
    replace: str


@dataclass
class FileResult:
    """What happened to one documentation file."""

    path: str
    applied: int = 0
    rejected: list[str] = field(default_factory=list)  # edits whose `find` was not unique
    reason: str = ""
    skipped: str = ""  # why the file was not sent to the model


@dataclass
class LLMResult:
    """Outcome of :func:`update_docs`; files are changed in the working tree."""

    files: list[FileResult] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return any(f.applied for f in self.files)

    def summary(self, pr: PullRequest, model: str) -> str:
        """Markdown for the docs pull request (or the comment when nothing changed)."""
        lines = [MARKER, f"Product docs check for #{pr.number} by docdrift with `{model}`.", ""]
        if not self.changed:
            lines += ["**No documentation update needed.**", ""]
        for f in self.files:
            if f.skipped:
                lines.append(f"- `{f.path}`: skipped - {f.skipped}")
                continue
            state = f"{f.applied} edit(s)" if f.applied else "unchanged"
            lines.append(f"- `{f.path}`: {state}. {f.reason}".rstrip())
            for find in f.rejected:
                lines.append(f"  - edit not applied (text not found exactly once): `{_shorten(find, 80)}`")
        lines += [
            "",
            "Review every change: the model only edits README / docs_paths, but it can be wrong.",
        ]
        return "\n".join(lines) + "\n"


def _shorten(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class LLMClient:
    """Minimal client for an OpenAI-compatible chat completions endpoint.

    ``base_url`` is the API root, e.g. ``https://openrouter.ai/api/v1``; the
    key is optional (a local LM Studio needs none).
    """

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: int = 180) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout

    def chat(self, system: str, user: str) -> str:
        headers = {
            "Content-Type": "application/json",
            # Attribution headers for OpenRouter; other providers ignore them.
            "X-Title": "docdrift",
            "HTTP-Referer": "https://github.com/tomdevtech/docdrift",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            r = requests.post(
                self.url,
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "temperature": 0.2,
                },
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise LLMError(f"LLM not reachable at {self.url}: {exc}") from exc
        if not r.ok:
            raise LLMError(f"LLM answered {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError as exc:
            raise LLMError(
                f"{self.url} did not answer with JSON ({r.text[:100]!r}) - is llm_api_base_url right?"
            ) from exc
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            error = data.get("error") if isinstance(data, dict) else None
            raise LLMError(f"unexpected answer from the LLM: {error or str(data)[:300]}") from exc


# --- prompt and edits ------------------------------------------------------------


def parse_reply(reply: str) -> tuple[list[Edit], str]:
    """``(edits, reason)`` from the model's reply.

    Free models do not always return clean JSON: reasoning blocks, code fences and
    text around the object are tolerated - the outermost ``{...}`` is used.
    """
    text = _THINK.sub("", reply).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError(f"reply contains no JSON object: {_shorten(reply, 200)}")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LLMError(f"reply is not valid JSON: {_shorten(reply, 200)}") from exc
    if not isinstance(data, dict):
        raise LLMError("reply is not a JSON object")
    edits = [
        Edit(str(e["find"]), str(e.get("replace", "")))
        for e in data.get("edits") or []
        if isinstance(e, dict) and e.get("find")
    ]
    return edits, str(data.get("reason") or "").strip()


def apply_edits(text: str, edits: list[Edit]) -> tuple[str, int, list[str]]:
    """Apply edits whose ``find`` occurs exactly once; return ``(text, applied, rejected)``."""
    applied, rejected = 0, []
    for edit in edits:
        if edit.find == edit.replace:
            continue
        if text.count(edit.find) != 1:
            rejected.append(edit.find)
            continue
        text = text.replace(edit.find, edit.replace, 1)
        applied += 1
    return text, applied, rejected


def select_files(task: CopilotTask, max_files: int) -> list[str]:
    """Doc files to check: those with mentions (most first), then ``README.md``."""
    counts: dict[str, int] = {}
    for m in task.docs_check.mentions:
        counts[m.path] = counts.get(m.path, 0) + 1
    ordered = sorted(counts, key=lambda p: (-counts[p], p))
    if "README.md" in task.docs_check.doc_files and "README.md" not in ordered:
        ordered.append("README.md")
    if not ordered:
        ordered = task.docs_check.doc_files[:1]
    return ordered[: max(max_files, 0)]


def build_prompt(task: CopilotTask, pr: PullRequest, path: str, content: str, limit: int) -> str | None:
    """User prompt for one file; ``None`` if the file alone does not fit ``limit``."""
    reasons = "\n".join(f"- {r}" for r in task.docs_check.reasons) or "- (pre-check disabled)"
    mentions = "\n".join(m.render() for m in task.docs_check.mentions if m.path == path) or "(none)"
    head = (
        f"Merged pull request #{pr.number}: {_shorten(pr.title, 120) or 'untitled'}\n\n"
        f"## Pre-check hints\n\n{reasons}\n\n"
        f"## Lines of this file that mention changed code\n\n{mentions}\n\n"
    )
    tail = f"## File `{path}`\n\n````\n{content}\n````\n"
    room = limit - len(head) - len(tail) - 200
    if room < 500:
        return None
    structure = task.structure.strip() or "(none)"
    if len(structure) > room:
        structure = structure[:room] + "\n... (truncated)"
    return f"{head}## Structural changes (AST)\n\n````text\n{structure}\n````\n\n{tail}"


# --- public API ------------------------------------------------------------------


def update_docs(
    repo_root: Path | str,
    cfg: DocdriftConfig,
    task: CopilotTask,
    pr: PullRequest,
    *,
    client,
    log: Log = print,
) -> LLMResult:
    """Ask the LLM file by file and apply its edits in the working tree.

    Args:
        repo_root: checkout of the merge result; doc files are edited in place.
        cfg: configuration (``github_docs.llm_*``).
        task: the task from :func:`docdrift.merge.task.build_copilot_task`.
        pr: the merged pull request.
        client: object with ``chat(system, user) -> str`` (e.g. :class:`LLMClient`).
        log: progress callback.

    Raises:
        LLMError: the LLM failed (rate limit, wrong key or URL, unusable answer).
    """
    repo_root = Path(repo_root)
    gd = cfg.github_docs
    result = LLMResult()
    for path in select_files(task, gd.llm_max_files):
        file = repo_root / path
        if not file.is_file():
            result.files.append(FileResult(path, skipped="file not found"))
            continue
        content = file.read_text(encoding="utf-8")
        prompt = build_prompt(task, pr, path, content, gd.llm_max_chars)
        if prompt is None:
            result.files.append(FileResult(path, skipped=f"too large for llm_max_chars ({gd.llm_max_chars})"))
            log(f"{path}: skipped - too large for the prompt")
            continue
        log(f"{path}: asking {gd.llm_model} ...")
        edits, reason = parse_reply(client.chat(SYSTEM_PROMPT, prompt))
        new, applied, rejected = apply_edits(content, edits)
        if applied:
            file.write_text(new, encoding="utf-8", newline="")
        log(f"{path}: {applied} edit(s) applied, {len(rejected)} rejected")
        result.files.append(FileResult(path, applied=applied, rejected=rejected, reason=reason))
    return result


__all__ = [
    "Edit",
    "FileResult",
    "LLMClient",
    "LLMError",
    "LLMResult",
    "apply_edits",
    "build_prompt",
    "parse_reply",
    "select_files",
    "update_docs",
]
