"""Thin wrapper around the ``git`` CLI.

Uses ``subprocess`` instead of a library such as GitPython to avoid a large
dependency. All output is decoded as UTF-8 (``errors="replace"``) so the tool
behaves the same on Windows, where the default console encoding is not UTF-8.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# Git's well-known "empty tree" object. Used as the base of the very first run
# so that *every* file of the repository counts as added (diffing against the
# first commit would silently skip all files that were created in it).
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"


@dataclass
class Changes:
    """Files that differ between two commits, grouped by what happened to them."""

    added: list[Path] = field(default_factory=list)
    modified: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)

    @property
    def existing(self) -> list[Path]:
        """Files that exist at the target commit (added or modified)."""
        return self.added + self.modified

    @property
    def all(self) -> list[Path]:
        """Every touched path, including deleted ones."""
        return self.added + self.modified + self.deleted


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def get_current_head(repo_root: Path) -> str:
    """Return the SHA of the current ``HEAD``."""
    return _run_git(repo_root, ["rev-parse", "HEAD"]).strip()


def get_changes(repo_root: Path, from_sha: str, to_sha: str) -> Changes:
    """Return the files changed between two commits (renames count as delete + add)."""
    # -z: NUL-separated, so paths with spaces or non-ASCII characters are not quoted.
    output = _run_git(repo_root, ["diff", "--name-status", "--no-renames", "-z", from_sha, to_sha])
    tokens = [t for t in output.split("\0") if t]
    changes = Changes()
    for status, path in zip(tokens[0::2], tokens[1::2]):
        target = {"A": changes.added, "D": changes.deleted}.get(status[0], changes.modified)
        target.append(Path(path))
    return changes


def get_file_diff(repo_root: Path, path: Path, from_sha: str, to_sha: str) -> str:
    """Return the unified diff of one file between two commits."""
    return _run_git(repo_root, ["diff", from_sha, to_sha, "--", path.as_posix()])


def read_file(repo_root: Path, path: Path, sha: str) -> str | None:
    """Return a file's content at ``sha``, or ``None`` if it does not exist there.

    ``None`` (instead of ``""``) lets callers tell "file absent" from "file empty".
    A leading UTF-8 byte order mark is removed.
    """
    try:
        content = _run_git(repo_root, ["show", f"{sha}:{path.as_posix()}"])
    except subprocess.CalledProcessError:
        return None
    # A UTF-8 byte order mark (common with Windows editors) breaks ast.parse.
    return content.removeprefix("\ufeff")


def list_files(repo_root: Path, sha: str) -> list[Path]:
    """Return every tracked file at ``sha``."""
    output = _run_git(repo_root, ["ls-tree", "-r", "--name-only", "-z", sha])
    return [Path(p) for p in output.split("\0") if p]


def get_commit_messages(repo_root: Path, from_sha: str, to_sha: str) -> list[str]:
    """Return the full commit messages in ``from_sha..to_sha`` (newest first)."""
    revision = to_sha if from_sha == EMPTY_TREE else f"{from_sha}..{to_sha}"
    output = _run_git(repo_root, ["log", "--format=%B%x00", revision])
    return [m.strip() for m in output.split("\0") if m.strip()]


def merge_base(repo_root: Path, a: str, b: str) -> str:
    """Return the best common ancestor of two commits (``git merge-base``).

    The diff ``merge_base(base, head)..head`` contains exactly the changes of a
    pull request, even when ``main`` moved on in the meantime.
    """
    return _run_git(repo_root, ["merge-base", a, b]).strip()


def commit_exists(repo_root: Path, sha: str) -> bool:
    """True if ``sha`` names a commit in the repository (e.g. not lost to a rebase + gc)."""
    try:
        _run_git(repo_root, ["cat-file", "-e", f"{sha}^{{commit}}"])
    except subprocess.CalledProcessError:
        return False
    return True


def is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    """True if ``ancestor`` is reachable from ``descendant`` (``git merge-base --is-ancestor``)."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=repo_root, capture_output=True, check=False,
    )
    return result.returncode == 0
