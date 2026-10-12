"""Pre-check of the merge flow: could this change have made the product docs outdated?

Runs before the Copilot coding agent is asked, so merges that cannot affect users
(bug fixes inside a function, private helpers, tests, formatting) cost no Copilot
request at all. The check is deliberately *generous*: it only has to rule out the
obvious no-ops. Whether an update is really needed is decided by Copilot, which is
told to change nothing when the docs are still correct.

Signals that the docs *may* need an update (each becomes a "reason"):

* public API of a public module changed (added/removed/renamed/moved/re-signed)
* a public module was added or removed
* a packaging manifest changed (entry points, Python version, dependencies)
* a file changed that cannot be analysed (templates, YAML, shell, other languages)
* the commit messages announce a feature or a breaking change
* the documentation mentions a symbol or file that changed in any way - this also
  catches behaviour changes behind an unchanged signature

The mentions are passed on to Copilot as exact ``file:line`` starting points.
Pure functions, no git calls: :mod:`docdrift.merge` supplies the inputs.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from docdrift.adr import is_manifest
from docdrift.analysis import API_KINDS, ChangeSet, is_public, module_name
from docdrift.git_utils import Changes

#: Technical documentation that may live in a repository (hand-written ADRs).
#: Never product docs: Copilot neither reads it as scope nor edits it.
TECHNICAL_DOC_DIRS = ["docs/adr/"]

#: Files below the docs paths that count as documentation text.
DOC_SUFFIXES = {".md", ".markdown", ".rst", ".txt", ".adoc"}
_MAX_MENTIONS = 40
_FEATURE = re.compile(r"^feat(\([^)]*\))?!?:", re.MULTILINE | re.IGNORECASE)
_BREAKING = re.compile(r"BREAKING[ -]CHANGE|^\w+(\([^)]*\))?!:", re.MULTILINE)
_FENCE = re.compile(r"^\s*(```|~~~)")

_WHAT = {
    "added": "new",
    "removed": "removed",
    "signature": "signature changed",
    "body": "implementation changed",
    "renamed": "renamed",
    "moved_in": "moved here",
    "moved_out": "moved away",
}


@dataclass
class DocMention:
    """A line of the documentation that mentions changed code."""

    path: str
    line: int
    name: str
    what: str
    text: str

    def render(self) -> str:
        snippet = self.text.strip()
        if len(snippet) > 100:
            snippet = snippet[:97] + "..."
        return f"- `{self.path}:{self.line}` mentions `{self.name}` ({self.what}): {snippet}"


@dataclass
class DocsCheck:
    """Result of :func:`check_docs`."""

    reasons: list[str] = field(default_factory=list)
    mentions: list[DocMention] = field(default_factory=list)
    doc_files: list[str] = field(default_factory=list)

    @property
    def needed(self) -> bool:
        """True if anything suggests the product docs could be outdated."""
        return bool(self.reasons)


# --- inputs ---------------------------------------------------------------------


def is_doc_file(path: Path | str, docs_paths: list[str]) -> bool:
    """True for documentation text below ``docs_paths``; technical docs never count."""
    p = PurePosixPath(Path(path).as_posix())
    text = p.as_posix()
    if p.suffix.lower() not in DOC_SUFFIXES or any(text.startswith(d) for d in TECHNICAL_DOC_DIRS):
        return False
    return any(
        text == pattern.rstrip("/")
        or fnmatch.fnmatch(text, pattern)
        or text.startswith(pattern.rstrip("/") + "/")
        for pattern in docs_paths
    )


def _is_housekeeping(path: str) -> bool:
    """Files that never change user-facing behaviour (``.gitignore``, ``LICENSE``, lock files)."""
    name = PurePosixPath(path).name
    return (
        name.startswith(".")
        or name.upper().startswith(("LICENSE", "COPYING"))
        or name.endswith(".lock")
    )


def _module_is_public(path: str) -> bool:
    name = module_name(path)
    return bool(name) and is_public(name)


# --- mentions -------------------------------------------------------------------


def _symbol_pattern(name: str) -> re.Pattern:
    """``name(`` anywhere, or ``name`` as a whole word inside inline code."""
    word = rf"(?<![\w]){re.escape(name)}(?![\w])"
    return re.compile(rf"(?<![\w.]){re.escape(name)}\s*\(|`[^`\n]*{word}[^`\n]*`")


def _changed_names(changeset: ChangeSet, changes: Changes) -> dict[str, str]:
    """Names worth searching for in the docs -> what happened to them."""
    names: dict[str, str] = {}
    for fc in changeset.files.values():
        if fc.status == "unchanged" or not fc.path.endswith(".py") or not _module_is_public(fc.path):
            continue
        for c in fc.changes:
            if c.kind not in _WHAT or not is_public(c.name):
                continue
            short = c.name.split(".")[-1]
            if len(short) >= 3 and not short.startswith("__"):
                names.setdefault(short, _WHAT[c.kind])
    for p in changes.all:
        fc = changeset.files.get(p.as_posix())
        if (fc is not None and fc.status == "unchanged") or _is_housekeeping(p.as_posix()):
            continue
        names.setdefault(p.name, "file changed")
    return names


def find_mentions(docs: dict[str, str], names: dict[str, str]) -> list[DocMention]:
    """Lines in ``docs`` (path -> text) that mention one of ``names``.

    Symbols count inside inline code, as a call ``name(``, or anywhere in a fenced
    code block; file names count anywhere (``pipeline.py``).
    """
    symbol_patterns = {
        n: _symbol_pattern(n) for n, w in names.items() if w != "file changed"
    }
    file_patterns = {
        n: re.compile(rf"(?<![\w.-]){re.escape(n)}(?![\w-])") for n, w in names.items()
        if w == "file changed"
    }
    found: list[DocMention] = []
    for path in sorted(docs):
        in_fence = False
        for number, line in enumerate(docs[path].splitlines(), start=1):
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            for name, pattern in symbol_patterns.items():
                hit = (
                    re.search(rf"(?<![\w]){re.escape(name)}(?![\w])", line)
                    if in_fence
                    else pattern.search(line)
                )
                if hit:
                    found.append(DocMention(path, number, name, names[name], line))
            for name, pattern in file_patterns.items():
                if pattern.search(line):
                    found.append(DocMention(path, number, name, "file changed", line))
            if len(found) >= _MAX_MENTIONS:
                return found
    return found


# --- the check ------------------------------------------------------------------


def check_docs(
    changeset: ChangeSet,
    changes: Changes,
    messages: list[str],
    docs: dict[str, str],
) -> DocsCheck:
    """Collect the reasons why the docs in ``docs`` (path -> text) may be outdated.

    ``changes``/``changeset`` must already exclude ignored and documentation files.
    """
    result = DocsCheck(doc_files=sorted(docs))

    for path in sorted(changeset.files):
        fc = changeset.files[path]
        if not path.endswith(".py") or not _module_is_public(path):
            continue
        if fc.status in ("added", "removed"):
            word = "added" if fc.status == "added" else "removed"
            result.reasons.append(f"public module `{path}` {word}")
            continue
        api = sorted(
            {c.name for c in fc.changes if c.kind in API_KINDS and is_public(c.name)}
        )
        if api:
            listed = ", ".join(f"`{n}`" for n in api[:6]) + (" ..." if len(api) > 6 else "")
            result.reasons.append(f"public API of `{path}` changed: {listed}")

    manifests = [p.as_posix() for p in changes.all if is_manifest(p)]
    if manifests:
        result.reasons.append(
            "packaging manifest changed (entry points, dependencies, Python version?): "
            + ", ".join(f"`{m}`" for m in manifests)
        )

    opaque = [
        path for path, fc in sorted(changeset.files.items())
        if fc.status == "unsupported" and path not in manifests and not _is_housekeeping(path)
    ]
    if opaque:
        listed = ", ".join(f"`{p}`" for p in opaque[:8]) + (" ..." if len(opaque) > 8 else "")
        result.reasons.append(f"files without structural analysis changed: {listed}")

    text = "\n".join(messages)
    if _BREAKING.search(text):
        result.reasons.append("commit messages announce a breaking change")
    elif _FEATURE.search(text):
        result.reasons.append("commit messages announce a new feature")

    result.mentions = find_mentions(docs, _changed_names(changeset, changes))
    if result.mentions:
        names = sorted({m.name for m in result.mentions})
        result.reasons.append(
            "the documentation mentions changed code: " + ", ".join(f"`{n}`" for n in names[:8])
        )
    return result
