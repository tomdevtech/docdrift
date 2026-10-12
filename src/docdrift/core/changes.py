"""Stage 0, shared by both flows: which files changed, and how (no LLM, no vault).

* :func:`collect_changes` - diff a commit range, drop ignored and binary files,
  run the AST analysis (:mod:`docdrift.core.analysis`).
* :func:`should_ignore`, :func:`is_manifest` - path classification.

The local flow (:mod:`docdrift.local`) and the merge flow (:mod:`docdrift.merge.task`)
both build on this module and never on each other.
"""

from __future__ import annotations

import fnmatch
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from docdrift.core import analysis, git_utils
from docdrift.core.config import DocdriftConfig

#: Progress callback used by both flows.
Log = Callable[[str], None]


@dataclass
class Snapshot:
    """Everything known about a commit range before any LLM is involved."""

    changes: git_utils.Changes
    changeset: analysis.ChangeSet
    new_sources: dict[str, str]


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


_MANIFEST_NAMES = {"pyproject.toml", "setup.cfg", "setup.py", "package.json", "Pipfile"}


def is_manifest(path: Path) -> bool:
    """True for dependency or packaging manifests (pyproject.toml, package.json, ...)."""
    name = path.name
    return name in _MANIFEST_NAMES or (name.startswith("requirements") and name.endswith(".txt"))


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
