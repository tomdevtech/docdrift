"""Orchestration of a documentation run - the library entry point.

A run has four stages. Stage 0 is deterministic, stages 1-3 call the LLM:

0. **Analyse**   git diff -> :class:`~docdrift.analysis.ChangeSet` (AST based).
1. **Per file**  one note per changed file (``Projects/<repo>/<file>.md``).
2. **ADR**       if the change is significant (:func:`docdrift.adr.assess`), draft an ADR.
3. **Overview**  regenerate ``_Architecture.md`` when the project's public
   structure changed (see :meth:`ProjectModel.fingerprint`).

State handling: the "last documented commit" only advances when *every* stage
succeeded, so a failed run is retried next time. Retries are cheap: every note
stores the hash of the content it describes (``source_hash``), and notes that are
already current are skipped without an LLM call. ADR drafts are created at most
once per target commit and never for the initial import. A lock file prevents
two runs at once.

The CLI (:mod:`docdrift.cli`) is a thin wrapper around :func:`run_documentation`
and :func:`draft_adr`; both can be used directly from Python.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import frontmatter

from docdrift import adr as adr_module
from docdrift import analysis, git_utils
from docdrift import state as state_module
from docdrift.config import DocdriftConfig, load_config
from docdrift.llm_client import ChatMessage, LLMClient, LLMUnavailable
from docdrift.playbook_loader import load_playbook, resolve_playbook_name
from docdrift.vault import (
    AUTO_DOC_MARKER,
    Vault,
    adr_note_path,
    architecture_note_path,
    open_vault,
    resolve_note_path,
)

Log = Callable[[str], None]


@dataclass
class RunResult:
    """Outcome of :func:`run_documentation`."""

    documented: list[str] = field(default_factory=list)  # files whose note was written
    skipped: list[str] = field(default_factory=list)  # formatting-only or note already current
    deleted: list[str] = field(default_factory=list)  # files whose note was marked as deleted
    failed: list[str] = field(default_factory=list)  # files (or "ARCHITECTURE") that failed
    architecture_updated: bool = False
    adr_drafts: list[str] = field(default_factory=list)  # vault paths of new ADR drafts
    up_to_date: bool = False  # nothing to do

    @property
    def ok(self) -> bool:
        """True if no LLM stage failed."""
        return not self.failed


@dataclass
class Snapshot:
    """Everything known about a commit range before any LLM is involved."""

    changes: git_utils.Changes
    changeset: analysis.ChangeSet
    new_sources: dict[str, str]


# --- helpers --------------------------------------------------------------------


#: docdrift's own files are never documented, whatever the user's ``ignore`` list says.
ALWAYS_IGNORED = [".docdrift/"]


def should_ignore(path: Path, patterns: list[str]) -> bool:
    """True if ``path`` matches an ignore glob or lies below an ignored directory."""
    text = path.as_posix()
    return any(
        fnmatch.fnmatch(text, p) or text.startswith(p.rstrip("/") + "/")
        for p in [*patterns, *ALWAYS_IGNORED]
    )


def _is_text(content: str) -> bool:
    return "\0" not in content


def _strip_fence(text: str) -> str:
    """Remove a Markdown code fence wrapped around the whole reply (common with small models)."""
    match = re.fullmatch(r"\s*```(?:markdown|md)?[ \t]*\n(.*?)\n```\s*", text, flags=re.DOTALL)
    return match.group(1) if match else text


def parse_note(reply: str, metadata: dict) -> tuple[dict, str]:
    """Split an LLM reply into ``(front matter, body)``.

    The front matter is always set by docdrift (the model must not invent dates);
    a front matter block written by the model is dropped.
    """
    post = frontmatter.loads(_strip_fence(reply).strip())
    if not post.content.strip():
        raise ValueError("the LLM reply contains no note text")
    meta = {**metadata, "updated": date.today().isoformat()}
    return meta, post.content


def source_hash(content: str) -> str:
    """Hash of a file's content, stored in its note to skip unchanged files."""
    return hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... (truncated)"


def build_file_prompt(
    repo_name: str,
    path: str,
    source: str,
    diff: str,
    existing_doc: str,
    structure: str,
    limit: int,
) -> str:
    """Assemble the user prompt for one file, shrinking it to ``limit`` characters.

    Degradation order: (1) replace the full source by a signature outline - the
    AST view is much smaller than the code; (2) cut diff and existing note.
    """
    existing = existing_doc.split(AUTO_DOC_MARKER, 1)[0].strip()  # never show manual notes

    def assemble(src: str, dif: str, doc: str) -> str:
        return (
            f"# Task: file documentation\n\nProject: {repo_name}\nFile: {path}\n"
            f"Today: {date.today().isoformat()}\n\n"
            f"## Structural analysis (computed from the AST)\n\n{structure}\n\n"
            f"## Diff since the last documentation run\n\n````diff\n{dif}\n````\n\n"
            f"## Existing documentation\n\n{doc or '(none - new file or first run)'}\n\n"
            f"## Current source code\n\n````\n{src}\n````\n\n"
            "Update the documentation according to the playbook."
        )

    prompt = assemble(source, diff, existing)
    if len(prompt) <= limit:
        return prompt

    outline = analysis.file_outline(path, source) or _cut(source, limit // 4)
    shrunk_source = f"(file too large for the prompt - signature outline only)\n{outline}"
    prompt = assemble(shrunk_source, diff, existing)
    if len(prompt) <= limit:
        return prompt

    budget = max(limit - len(assemble(shrunk_source, "", "")), 2000)
    return assemble(shrunk_source, _cut(diff, budget // 2), _cut(existing, budget // 2))


def collect_changes(
    repo_root: Path,
    cfg: DocdriftConfig,
    from_sha: str,
    to_sha: str,
    extra_ignore: list[str] | None = None,
) -> Snapshot | None:
    """Stage 0: diff the range and analyse it. ``None`` if nothing relevant changed.

    ``extra_ignore`` adds patterns on top of ``cfg.ignore`` (the merge flow uses it
    to leave out the documentation files themselves).
    """
    raw = git_utils.get_changes(repo_root, from_sha, to_sha)
    patterns = [*cfg.ignore, *(extra_ignore or [])]

    def wanted(paths: list[Path], sha: str) -> dict[str, str]:
        out = {}
        for p in paths:
            content = git_utils.read_file(repo_root, p, sha)
            if content is not None and _is_text(content):
                out[p.as_posix()] = content
        return out

    kept = git_utils.Changes(
        added=[p for p in raw.added if not should_ignore(p, patterns)],
        modified=[p for p in raw.modified if not should_ignore(p, patterns)],
        deleted=[p for p in raw.deleted if not should_ignore(p, patterns)],
    )
    new_sources = wanted(kept.existing, to_sha)
    old_sources = wanted(kept.modified + kept.deleted, from_sha)

    # Drop binary files from the change lists.
    kept.added = [p for p in kept.added if p.as_posix() in new_sources]
    kept.modified = [p for p in kept.modified if p.as_posix() in new_sources]
    kept.deleted = [p for p in kept.deleted if p.as_posix() in old_sources]
    if not kept.all:
        return None
    return Snapshot(kept, analysis.analyze_changes(old_sources, new_sources), new_sources)


def _project_model(repo_root: Path, cfg: DocdriftConfig, sha: str) -> analysis.ProjectModel:
    sources = {}
    for p in git_utils.list_files(repo_root, sha):
        if p.suffix == ".py" and not should_ignore(p, cfg.ignore):
            content = git_utils.read_file(repo_root, p, sha)
            if content is not None:
                sources[p.as_posix()] = content
    return analysis.build_project_model(sources)


# --- stages ---------------------------------------------------------------------


def _document_file(
    *, repo_root: Path, cfg: DocdriftConfig, client, vault: Vault, snap: Snapshot,
    path: Path, from_sha: str, to_sha: str, force: bool = False,
) -> bool:
    """Stage 1 for one file: build the prompt, call the LLM, write the note.

    Returns ``False`` without an LLM call if the note already describes exactly
    this content (``source_hash`` in its front matter) - after a failed or
    repeated run, or when switching branches back and forth.
    """
    key = path.as_posix()
    note = resolve_note_path(cfg.repo_name, path)
    meta_old, existing = vault.read_note(note)
    digest = source_hash(snap.new_sources[key])
    if not force and meta_old.get("source_hash") == digest and not meta_old.get("deleted"):
        return False
    prompt = build_file_prompt(
        cfg.repo_name,
        key,
        snap.new_sources[key],
        git_utils.get_file_diff(repo_root, path, from_sha, to_sha),
        existing,
        snap.changeset.render(key),
        cfg.max_prompt_chars,
    )
    reply = client.chat(
        [
            ChatMessage("system", load_playbook(resolve_playbook_name(path), cfg.playbook_dir, cfg.language)),
            ChatMessage("user", prompt),
        ]
    )
    meta, body = parse_note(
        reply,
        {
            "project": cfg.repo_name,
            "file": key,
            "commit": to_sha[:12],
            "source_hash": digest,
            "tags": ["auto-doc", cfg.repo_name],
        },
    )
    vault.write_note(note, meta, body)
    return True


DELETED_NOTICE = "> [!warning] Deleted"


def _mark_deleted(*, cfg: DocdriftConfig, vault: Vault, path: Path, to_sha: str) -> bool:
    """Flag the note of a deleted file instead of leaving it silently outdated.

    The note is kept (history, your manual section); its front matter gets
    ``deleted`` and a warning is put on top. No LLM call. Returns ``False`` if
    there is no note or it is already marked.
    """
    note = resolve_note_path(cfg.repo_name, path)
    meta, body = vault.read_note(note)
    if not meta and not body:
        return False
    if meta.get("deleted"):
        return False
    generated = body.split(AUTO_DOC_MARKER, 1)[0].strip()
    notice = (
        f"{DELETED_NOTICE}\n> `{path.as_posix()}` was deleted in commit `{to_sha[:12]}` "
        f"({date.today().isoformat()}). This note describes its last version.\n\n"
    )
    tags = [t for t in meta.get("tags", []) if t != "deleted"] + ["deleted"]
    vault.write_note(note, {**meta, "deleted": date.today().isoformat(), "tags": tags}, notice + generated)
    return True


def _update_architecture(
    *, repo_root: Path, cfg: DocdriftConfig, client, vault: Vault,
    st: state_module.State, snap: Snapshot, to_sha: str,
) -> bool:
    """Stage 3: regenerate ``_Architecture.md`` if the public structure changed.

    The LLM only writes the prose (overview, components, data flow); the module
    map is drawn deterministically from the import graph so it cannot be wrong.
    """
    note = architecture_note_path(cfg.repo_name)
    _, existing = vault.read_note(note)
    touched_python = any(p.suffix == ".py" for p in snap.changes.all)
    if existing and st.fingerprint and not touched_python:
        return False  # cheap exit for pushes that did not touch code

    model = _project_model(repo_root, cfg, to_sha)
    if not model.modules:
        return False
    fingerprint = model.fingerprint()
    if existing and fingerprint == st.fingerprint:
        return False

    prompt = (
        "# Task: architecture overview\n\n"
        f"Project: {cfg.repo_name}\n\n"
        "Entry points (modules that no other module imports):\n"
        + "\n".join(f"- {p}" for p in model.entry_points())
        + "\n\n## Module outline (public API and internal imports)\n\n"
        + model.render_outline(cfg.max_prompt_chars)
    )
    reply = client.chat(
        [
            ChatMessage("system", load_playbook("architecture", cfg.playbook_dir, cfg.language)),
            ChatMessage("user", prompt),
        ]
    )
    _, prose = parse_note(reply, {})
    body = f"{prose.strip()}\n\n## Module map\n\n```mermaid\n{model.render_mermaid()}\n```\n"
    if cfg.adr.enabled:
        body += f"\nSee also: [[{adr_note_path(cfg.repo_name, 'Index')}|ADR drafts]]\n"
    meta = {
        "project": cfg.repo_name,
        "type": "architecture",
        "tags": ["auto-doc", "architecture", cfg.repo_name],
    }
    vault.write_note(note, {**meta, "updated": date.today().isoformat()}, body)
    st.fingerprint = fingerprint
    return True


# --- public API -----------------------------------------------------------------


def resolve_start(repo_root: Path, last_sha: str | None, to_sha: str, log: Log = print) -> str:
    """Where a run starts when no ``--from`` was given.

    * no state yet -> Git's empty tree (document everything);
    * the last documented commit no longer exists (rebase + gc) -> empty tree;
      notes whose content is unchanged are skipped by their ``source_hash``;
    * it lies on another branch -> the merge base, so the diff describes the
      current branch instead of undoing the other one.
    """
    if not last_sha:
        return git_utils.EMPTY_TREE
    if not git_utils.commit_exists(repo_root, last_sha):
        log(f"Last documented commit {last_sha[:8]} no longer exists - checking all files.")
        return git_utils.EMPTY_TREE
    if git_utils.is_ancestor(repo_root, last_sha, to_sha):
        return last_sha
    base = git_utils.merge_base(repo_root, last_sha, to_sha)
    log(f"Last documented commit {last_sha[:8]} is on another branch - starting at {base[:8]}.")
    return base


def run_documentation(
    repo_root: Path | str,
    cfg: DocdriftConfig | None = None,
    from_sha: str | None = None,
    to_sha: str | None = None,
    *,
    client=None,
    vault: Vault | None = None,
    log: Log = print,
    force: bool = False,
) -> RunResult:
    """Document everything that changed since the last run.

    Args:
        repo_root: root of the target Git repository.
        cfg: configuration; loaded from ``.docdrift/config.yaml`` if omitted.
        from_sha: start of the range (default: see :func:`resolve_start`).
        to_sha: end of the range (default: ``HEAD``).
        client: object with ``chat(messages) -> str`` (default: :class:`LLMClient`).
        vault: note storage (default: from the config).
        log: progress callback (default: ``print``).
        force: regenerate notes even if their ``source_hash`` says they are current.

    Raises:
        state.RunLocked: another run is active for this repository.
    """
    repo_root = Path(repo_root)
    cfg = cfg or load_config(repo_root)
    state_dir = repo_root / ".docdrift" / "state"
    with state_module.run_lock(state_dir):
        return _run(repo_root, cfg, from_sha, to_sha, client, vault, log, force, state_dir)


def _run(repo_root, cfg, from_sha, to_sha, client, vault, log, force, state_dir) -> RunResult:
    to_sha = to_sha or git_utils.get_current_head(repo_root)
    st = state_module.load_state(state_dir, cfg.repo_name)
    from_sha = from_sha or resolve_start(repo_root, st.last_sha, to_sha, log)

    result = RunResult()
    if from_sha == to_sha:
        log("Nothing new to document.")
        result.up_to_date = True
        return result

    snap = collect_changes(repo_root, cfg, from_sha, to_sha)
    if snap is None:
        log("No relevant changes found.")
        st.last_sha = to_sha
        state_module.save_state(state_dir, cfg.repo_name, st)
        result.up_to_date = True
        return result

    client = client or LLMClient(cfg.api_base_url, cfg.model)
    vault = vault or open_vault(cfg)

    # Stage 1: one note per changed file.
    todo = snap.changes.existing
    for n, path in enumerate(todo, start=1):
        key = path.as_posix()
        prefix = f"[{n}/{len(todo)}] {key}"
        file_change = snap.changeset.files.get(key)
        if file_change is not None and file_change.status == "unchanged":
            log(f"{prefix}: formatting/comments only - skipped")
            result.skipped.append(key)
            continue
        try:
            written = _document_file(
                repo_root=repo_root, cfg=cfg, client=client, vault=vault, snap=snap,
                path=path, from_sha=from_sha, to_sha=to_sha, force=force,
            )
        except LLMUnavailable as exc:
            log(f"{prefix}: {exc}")
            log("Stopping - the remaining files are documented on the next run.")
            result.failed.extend(p.as_posix() for p in todo[n - 1:])
            break
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the run
            log(f"{prefix}: failed - {exc}")
            result.failed.append(key)
            continue
        if written:
            log(f"{prefix}: documented")
            result.documented.append(key)
        else:
            log(f"{prefix}: note already current - skipped")
            result.skipped.append(key)

    for path in snap.changes.deleted:
        try:
            if _mark_deleted(cfg=cfg, vault=vault, path=path, to_sha=to_sha):
                log(f"{path.as_posix()}: deleted - note marked")
                result.deleted.append(path.as_posix())
        except Exception as exc:  # noqa: BLE001 - reported, retried next run
            log(f"{path.as_posix()}: marking as deleted failed - {exc}")
            result.failed.append(path.as_posix())

    # Stage 2: ADR draft - not for the initial import, at most once per target commit.
    first_run = from_sha == git_utils.EMPTY_TREE
    if cfg.adr.enabled and not first_run and not any(a.get("to_sha") == to_sha for a in st.adrs):
        messages = git_utils.get_commit_messages(repo_root, from_sha, to_sha)
        signals = adr_module.assess(snap.changeset, snap.changes, messages)
        total = adr_module.score(signals)
        log(f"ADR score {total} (threshold {cfg.adr.threshold})")
        if total >= cfg.adr.threshold:
            log("Significant change - drafting an ADR ...")
            try:
                location = adr_module.create_draft(
                    repo_root=repo_root, cfg=cfg, client=client, vault=vault, st=st,
                    changeset=snap.changeset, commit_messages=messages, signals=signals,
                    from_sha=from_sha, to_sha=to_sha,
                )
                result.adr_drafts.append(location)
                log(f"  ADR draft written: {location}")
                state_module.save_state(state_dir, cfg.repo_name, st)  # numbering survives a later failure
            except Exception as exc:  # noqa: BLE001 - a bad draft must not block documentation
                log(f"  ADR draft failed: {exc} (retry with `docdrift adr --from {from_sha[:8]}`)")

    # Stage 3: architecture overview (after a clean stage 1, so it describes complete notes).
    if cfg.architecture_note and result.ok:
        log("Checking architecture overview ...")
        try:
            result.architecture_updated = _update_architecture(
                repo_root=repo_root, cfg=cfg, client=client, vault=vault,
                st=st, snap=snap, to_sha=to_sha,
            )
            if result.architecture_updated:
                log("  _Architecture.md updated")
        except Exception as exc:  # noqa: BLE001 - reported, retried next run
            log(f"  failed: {exc}")
            result.failed.append("ARCHITECTURE")

    if result.ok:
        st.last_sha = to_sha
    else:
        log("Some steps failed - they are retried on the next run (finished notes are kept).")
    state_module.save_state(state_dir, cfg.repo_name, st)
    log(
        f"Done. {len(result.documented)} documented, {len(result.skipped)} skipped, "
        f"{len(result.deleted)} marked deleted, {len(result.failed)} failed."
    )
    return result


def draft_adr(
    repo_root: Path | str,
    cfg: DocdriftConfig | None = None,
    from_sha: str | None = None,
    to_sha: str | None = None,
    *,
    client=None,
    vault: Vault | None = None,
    log: Log = print,
) -> str:
    """Draft an ADR for a commit range, ignoring the significance threshold.

    Does not touch ``last_sha``. Returns the vault path of the new draft.

    Raises:
        ValueError: if the range is empty or contains no relevant changes.
    """
    repo_root = Path(repo_root)
    cfg = cfg or load_config(repo_root)
    state_dir = repo_root / ".docdrift" / "state"
    with state_module.run_lock(state_dir):
        return _draft_adr(repo_root, cfg, from_sha, to_sha, client, vault, log, state_dir)


def _draft_adr(repo_root, cfg, from_sha, to_sha, client, vault, log, state_dir) -> str:
    to_sha = to_sha or git_utils.get_current_head(repo_root)
    st = state_module.load_state(state_dir, cfg.repo_name)
    from_sha = from_sha or resolve_start(repo_root, st.last_sha, to_sha, log)
    if from_sha == to_sha:
        raise ValueError("Empty range - pass --from <sha> (e.g. the commit before the feature).")

    snap = collect_changes(repo_root, cfg, from_sha, to_sha)
    if snap is None:
        raise ValueError("No relevant changes in this range (check the `ignore` patterns).")

    messages = git_utils.get_commit_messages(repo_root, from_sha, to_sha)
    signals = adr_module.assess(snap.changeset, snap.changes, messages)
    log(f"Significance score {adr_module.score(signals)} (threshold {cfg.adr.threshold}, ignored for manual drafts)")

    location = adr_module.create_draft(
        repo_root=repo_root, cfg=cfg, client=client or LLMClient(cfg.api_base_url, cfg.model),
        vault=vault or open_vault(cfg), st=st, changeset=snap.changeset,
        commit_messages=messages, signals=signals, from_sha=from_sha, to_sha=to_sha,
    )
    state_module.save_state(state_dir, cfg.repo_name, st)
    log(f"ADR draft written: {location}")
    return location
