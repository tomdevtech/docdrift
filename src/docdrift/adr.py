"""Architecture Decision Records (ADRs): detect significant changes, draft an ADR.

ADRs are *drafts*: the code shows **what** changed but not **why**, so a small
local model can only propose the decision and must mark the rationale as
unconfirmed. Drafts are written with ``status: proposed`` and are accepted (or
deleted) by a human; docdrift never sets ``accepted`` itself.

Whether a change is "big enough" is decided by deterministic signals
(:func:`assess`), not by the LLM. The LLM is only called once the score reaches
``adr.threshold``.

ADRs are technical documentation and therefore belong to the local flow only:
:func:`create_draft` writes them to ``Projects/<repo>/ADR/NNNN-slug.md`` in the
Obsidian vault, drafted by the local LLM. The merge flow (Copilot) never writes
ADRs - it only maintains the product docs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docdrift.analysis import ChangeSet
from docdrift.config import DocdriftConfig
from docdrift.git_utils import Changes
from docdrift.llm_client import ChatMessage
from docdrift.playbook_loader import load_playbook
from docdrift.state import State
from docdrift.vault import Vault, adr_note_path

_MANIFEST_NAMES = {"pyproject.toml", "setup.cfg", "setup.py", "package.json", "Pipfile"}
_BREAKING = re.compile(r"BREAKING[ -]CHANGE|^\w+(\([^)]*\))?!:", re.MULTILINE)
_ARCHITECTURAL = re.compile(r"\b(architecture|redesign|rewrite|migrat\w+)\b", re.IGNORECASE)
_FEATURE = re.compile(r"^(feat|refactor)(\(|:|!)", re.MULTILINE | re.IGNORECASE)


# --- significance -------------------------------------------------------------


@dataclass
class Signal:
    """One reason why a change might deserve an ADR."""

    points: int
    reason: str


def is_manifest(path: Path) -> bool:
    """True for dependency or packaging manifests (pyproject.toml, package.json, ...)."""
    name = path.name
    return name in _MANIFEST_NAMES or (name.startswith("requirements") and name.endswith(".txt"))


def assess(changeset: ChangeSet, changes: Changes, commit_messages: list[str]) -> list[Signal]:
    """Score how architecturally significant a range of changes is.

    Signals (points): Python modules added/removed (3), dependency manifest changed
    (3), public API changed in >= 3 files (2), symbols moved between files (2),
    commit messages announcing a breaking change (3) / architecture work (2) /
    a feature or refactor (1), and >= 10 files touched (1).
    """
    signals: list[Signal] = []

    added = [p for p in changes.added if p.suffix == ".py"]
    removed = [p for p in changes.deleted if p.suffix == ".py"]
    if added or removed:
        signals.append(Signal(3, f"{len(added)} Python module(s) added, {len(removed)} removed"))

    if any(is_manifest(p) for p in changes.all):
        signals.append(Signal(3, "dependency or packaging manifest changed"))

    api_files = changeset.public_api_files()
    if len(api_files) >= 3:
        signals.append(Signal(2, f"public API changed in {len(api_files)} files"))

    moved = changeset.count("moved_in")
    if moved:
        signals.append(Signal(2, f"{moved} symbol(s) moved between files"))

    text = "\n".join(commit_messages)
    if _BREAKING.search(text):
        signals.append(Signal(3, "commit message announces a breaking change"))
    elif _ARCHITECTURAL.search(text):
        signals.append(Signal(2, "commit messages mention architecture work"))
    elif _FEATURE.search(text):
        signals.append(Signal(1, "commit messages announce a feature or refactoring"))

    if len(changes.all) >= 10:
        signals.append(Signal(1, f"{len(changes.all)} files touched"))
    return signals


def score(signals: list[Signal]) -> int:
    """Sum of the signal points."""
    return sum(s.points for s in signals)


# --- draft parsing and rendering ----------------------------------------------


@dataclass
class AdrDraft:
    """The parts of an ADR that the LLM writes."""

    title: str
    context: str
    decision: str
    consequences: str


def parse_draft(text: str) -> AdrDraft:
    """Parse the LLM reply (``Title: ...`` plus Context/Decision/Consequences sections).

    Raises:
        ValueError: if the title or one of the three sections is missing.
    """
    parts = re.split(r"^##\s+(Context|Decision|Consequences)\s*$", text, flags=re.MULTILINE | re.IGNORECASE)
    preamble = parts[0]
    sections = {parts[i].lower(): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}
    missing = {"context", "decision", "consequences"} - sections.keys()
    if missing:
        raise ValueError(f"ADR draft is missing section(s): {', '.join(sorted(missing))}")

    title_match = re.search(r"^\s*(?:Title:|#)\s*(?:ADR\s*\d*:?\s*)?(.+?)\s*$", preamble, re.MULTILINE | re.IGNORECASE)
    if not title_match:
        raise ValueError("ADR draft has no title line")
    return AdrDraft(
        title=title_match.group(1).strip(" *`\""),
        context=sections["context"],
        decision=sections["decision"],
        consequences=sections["consequences"],
    )


def slugify(text: str) -> str:
    """``"Use AST analysis!"`` -> ``"use-ast-analysis"`` (max. 60 characters)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:60].strip("-") or "decision"


def render(
    draft: AdrDraft, number: int, repo_name: str, signals: list[Signal], commit_range: str
) -> tuple[dict, str]:
    """Build the front matter and Markdown body of an ADR note."""
    meta = {
        "type": "adr",
        "project": repo_name,
        "adr": number,
        "status": "proposed",
        "date": date.today().isoformat(),
        "tags": ["adr", repo_name],
    }
    reasons = "\n".join(f"- {s.reason}" for s in signals) or "- created manually"
    body = (
        f"# ADR {number:04d}: {draft.title}\n\n"
        f"> **Draft** generated by docdrift for `{commit_range}`. The rationale is inferred "
        f"from code and commit messages - fill in the \"please confirm\" spots, then set "
        f"`status: accepted` (or delete the note).\n\n"
        f"## Context\n\n{draft.context}\n\n"
        f"## Decision\n\n{draft.decision}\n\n"
        f"## Consequences\n\n{draft.consequences}\n\n"
        f"## Why this was flagged\n\n{reasons}\n"
    )
    return meta, body


# --- numbering -----------------------------------------------------------------


_INDEX_LINE = re.compile(r"^- \[\[(\d{4})-([^\]|]+)\]\] - (.*?)(?: \(([^)]*)\))?\s*$")


def _index_entries(vault: Vault, repo_name: str) -> dict[int, dict]:
    """ADRs listed in the vault index note, keyed by number.

    The index is the vault's own record of which numbers are taken, so numbering
    survives a lost ``.docdrift/state`` (new machine, CI runner, ``git clean``).
    """
    _, body = vault.read_note(adr_note_path(repo_name, "Index.md"))
    entries = {}
    for line in body.splitlines():
        if m := _INDEX_LINE.match(line.strip()):
            number = int(m.group(1))
            entries[number] = {"number": number, "slug": m.group(2), "title": m.group(3), "date": m.group(4) or ""}
    return entries


def _write_index(entries: dict[int, dict], repo_name: str, vault: Vault) -> None:
    """(Re)write the deterministic ADR index note - no LLM involved."""
    lines = [
        f"- [[{a['number']:04d}-{a['slug']}]] - {a['title']} ({a['date']})"
        for _, a in sorted(entries.items())
    ]
    meta = {"type": "adr-index", "project": repo_name, "tags": ["adr", repo_name]}
    body = "# Architecture decisions\n\n" + "\n".join(lines)
    vault.write_note(adr_note_path(repo_name, "Index.md"), meta, body)


# --- writing -------------------------------------------------------------------


def create_draft(
    *,
    repo_root: Path,
    cfg: DocdriftConfig,
    client,
    vault: Vault,
    st: State,
    changeset: ChangeSet,
    commit_messages: list[str],
    signals: list[Signal],
    from_sha: str,
    to_sha: str,
) -> str:
    """Ask the local LLM for an ADR draft and store it in the vault.

    Updates ``st.adrs`` and the index note. Returns the vault path of the draft.
    The caller is responsible for saving ``st``. ``repo_root`` is accepted for
    symmetry with the other stages; ADRs never touch the repository.

    Raises:
        ValueError: if the LLM reply cannot be parsed (nothing is written then).
    """
    known = _index_entries(vault, cfg.repo_name)
    for entry in st.adrs:
        known.setdefault(entry["number"], entry)

    prompt = (
        "# Task: ADR draft\n\n"
        f"Project: {cfg.repo_name}\n\n"
        "## Why this change was flagged\n\n"
        + "\n".join(f"- {s.reason}" for s in signals)
        + "\n\n## Commit messages\n\n"
        + "\n---\n".join(commit_messages)[:3000]
        + "\n\n## Structural changes (computed from the AST)\n\n"
        + changeset.render_all()[:6000]
        + "\n\n## Existing ADR titles (do not repeat these)\n\n"
        + ("\n".join(f"- {a['title']}" for _, a in sorted(known.items())) or "(none)")
    )
    reply = client.chat(
        [
            ChatMessage("system", load_playbook("adr", cfg.playbook_dir, cfg.language)),
            ChatMessage("user", prompt[: cfg.max_prompt_chars]),
        ]
    )
    draft = parse_draft(reply)

    number = max(known, default=0) + 1
    slug = slugify(draft.title)
    meta, body = render(draft, number, cfg.repo_name, signals, f"{from_sha[:8]}..{to_sha[:8]}")
    location = adr_note_path(cfg.repo_name, f"{number:04d}-{slug}.md")
    vault.write_note(location, meta, body)

    entry = {"number": number, "slug": slug, "title": draft.title, "date": meta["date"], "to_sha": to_sha}
    st.adrs.append(entry)
    known[number] = entry
    _write_index(known, cfg.repo_name, vault)
    return location
