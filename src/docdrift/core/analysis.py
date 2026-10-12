"""Deterministic structural analysis of Python code (no LLM, no Git, no I/O).

The LLM is good at writing prose and bad at working out *what* changed in a
large diff. This module does the second part with the stdlib ``ast`` module so
that the model only receives compact, reliable facts.

Three views are offered, all working on plain source strings:

* :func:`parse_module`        one file -> :class:`ModuleInfo` (symbols, imports, hashes)
* :func:`analyze_changes`     old/new sources -> :class:`ChangeSet` (what changed, per file)
* :func:`build_project_model` all sources -> :class:`ProjectModel` (outline,
  dependency graph, structural fingerprint)

Only Python is analysed. Any other file type (and Python files with syntax
errors) is reported as ``unsupported`` so callers fall back to the plain diff
instead of wrongly assuming "nothing changed".

Known limits: overloads and property setters share a qualified name, so only
the last definition is tracked; functions nested inside functions are covered
by the hash of their outer function but not listed separately.
"""

from __future__ import annotations

import ast
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: A removed and an added function are only reported as a rename/move if their
#: bodies are identical *and* at least this many AST nodes large (avoids
#: pairing unrelated ``pass`` stubs).
MIN_RENAME_WEIGHT = 6

#: Change kinds that alter a module's public surface (used for ADR scoring).
API_KINDS = {"added", "removed", "signature", "renamed", "moved_in", "moved_out"}


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


_EMPTY_HASH = _sha("")


def is_public(qualname: str) -> bool:
    """True if no component of a dotted name is private (``_x``; dunders count as public)."""
    return all(not (p.startswith("_") and not p.startswith("__")) for p in qualname.split("."))


# =============================================================================
# One file
# =============================================================================


@dataclass(frozen=True)
class Symbol:
    """A function, method or class of a module."""

    qualname: str  # "helper", "Foo", "Foo.run"
    kind: str  # "function" | "method" | "class"
    signature: str  # e.g. "def run(self, x: int = 1) -> str"
    #: Hash of the implementation without its docstring. For classes: hash of the
    #: class attributes / dataclass fields (methods are tracked as own symbols).
    body_hash: str = ""
    weight: int = 0  # number of AST nodes in the body (rename heuristic)
    summary: str = ""  # first line of the docstring

    @property
    def is_public(self) -> bool:
        return is_public(self.qualname)


@dataclass(frozen=True)
class ImportRef:
    """One imported name, e.g. ``from .x import y`` -> ``ImportRef("x", "y", 1)``."""

    module: str
    name: str = ""  # empty for a plain ``import module``
    level: int = 0  # number of leading dots of a relative import

    def render(self) -> str:
        if not self.name:
            return f"import {self.module}"
        return f"from {'.' * self.level}{self.module} import {self.name}"


@dataclass
class ModuleInfo:
    """Structural facts about one source file."""

    path: str
    parsed: bool = True  # False: not Python or not parseable (see ``reason``)
    reason: str = ""
    doc: str = ""  # first line of the module docstring
    module_hash: str = _EMPTY_HASH  # hash of the complete AST (ignores formatting/comments)
    rest_hash: str = _EMPTY_HASH  # hash of top-level code that is not def/class/import
    imports: list[ImportRef] = field(default_factory=list)
    symbols: dict[str, Symbol] = field(default_factory=dict)  # in source order


def _unparse(node: ast.AST | None) -> str:
    try:
        return ast.unparse(node) if node is not None else ""
    except Exception:  # noqa: BLE001  # pragma: no cover - defensive
        return "<?>"


def _first_line(doc: str | None) -> str:
    return doc.strip().splitlines()[0] if doc and doc.strip() else ""


def _decorators(node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef) -> str:
    return "".join(f"@{_unparse(d)} " for d in node.decorator_list)


def _function_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    # ast.unparse(node.args) renders positional-only, keyword-only, defaults,
    # *args and **kwargs correctly in one go.
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {_unparse(node.returns)}" if node.returns else ""
    return f"{_decorators(node)}{keyword} {node.name}({_unparse(node.args)}){returns}"


def _class_signature(node: ast.ClassDef) -> str:
    parts = [_unparse(b) for b in node.bases]
    parts += [f"{k.arg}={_unparse(k.value)}" if k.arg else f"**{_unparse(k.value)}" for k in node.keywords]
    bases = f"({', '.join(parts)})" if parts else ""
    return f"{_decorators(node)}class {node.name}{bases}"


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    first = body[0] if body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        return body[1:]
    return body


def _fingerprint(statements: list[ast.stmt]) -> tuple[str, int]:
    """Return ``(hash, node count)`` of statements; formatting-insensitive."""
    dumped = "|".join(ast.dump(s) for s in statements)
    return _sha(dumped), sum(1 for s in statements for _ in ast.walk(s))


def _collect_symbols(body: list[ast.stmt], prefix: str, out: dict[str, Symbol]) -> None:
    """Fill ``out`` with the functions, methods and classes found in ``body``."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qual = f"{prefix}{node.name}"
            digest, weight = _fingerprint(_without_docstring(node.body))
            out[qual] = Symbol(
                qual,
                "method" if prefix else "function",
                _function_signature(node),
                digest,
                weight,
                _first_line(ast.get_docstring(node)),
            )
        elif isinstance(node, ast.ClassDef):
            qual = f"{prefix}{node.name}"
            attributes = [
                s
                for s in _without_docstring(node.body)
                if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            ]
            out[qual] = Symbol(
                qual,
                "class",
                _class_signature(node),
                _fingerprint(attributes)[0],
                0,
                _first_line(ast.get_docstring(node)),
            )
            _collect_symbols(node.body, f"{qual}.", out)


def _collect_imports(tree: ast.Module) -> list[ImportRef]:
    """All imports of the module, including lazy ones inside functions."""
    refs: set[ImportRef] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.update(ImportRef(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            refs.update(ImportRef(node.module or "", a.name, node.level) for a in node.names)
    return sorted(refs, key=ImportRef.render)


def parse_module(path: str, source: str) -> ModuleInfo:
    """Parse one file. Non-Python files and syntax errors yield ``parsed=False``."""
    if not path.endswith(".py"):
        return ModuleInfo(path, parsed=False, reason="not a Python file")
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        return ModuleInfo(path, parsed=False, reason=f"could not be parsed: {exc}")

    rest = [
        n
        for n in tree.body
        if not isinstance(
            n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom)
        )
    ]
    info = ModuleInfo(
        path,
        doc=_first_line(ast.get_docstring(tree)),
        module_hash=_sha(ast.dump(tree)),
        rest_hash=_fingerprint(rest)[0],
        imports=_collect_imports(tree),
    )
    _collect_symbols(tree.body, "", info.symbols)
    return info


def file_outline(path: str, source: str) -> str:
    """Compact signature listing of one file (all symbols, with docstring summaries).

    Used instead of the full source when a file is too large for the prompt.
    Returns ``""`` for files that cannot be analysed.
    """
    info = parse_module(path, source)
    if not info.parsed:
        return ""
    lines = [info.doc] if info.doc else []
    for sym in info.symbols.values():
        doc = f"  # {sym.summary}" if sym.summary else ""
        lines.append(f"{'    ' * sym.qualname.count('.')}{sym.signature}{doc}")
    return "\n".join(lines)


# =============================================================================
# Changes between two versions
# =============================================================================

_MARKS = {
    "added": "+ added",
    "removed": "- removed",
    "signature": "~ signature",
    "body": "~ body",
    "renamed": "~ renamed",
    "moved_in": "~ moved in",
    "moved_out": "~ moved out",
    "import_added": "+ import",
    "import_removed": "- import",
    "other": "~ other",
}


@dataclass
class Change:
    """One detected change inside a file.

    ``kind`` is one of: ``added``, ``removed``, ``signature``, ``body`` (implementation
    changed, signature identical), ``renamed``, ``moved_in``, ``moved_out``,
    ``import_added``, ``import_removed``, ``other`` (module-level code).
    """

    kind: str
    name: str = ""
    detail: str = ""

    def render(self) -> str:
        mark = _MARKS[self.kind]
        if self.kind in ("added", "removed"):
            return f"{mark} {self.detail}"
        if self.kind.startswith("import"):
            return f"{mark} {self.name}"
        return f"{mark} {self.name}: {self.detail}"


@dataclass
class FileChange:
    """Analysis result for one file.

    ``status``: ``added`` | ``removed`` | ``changed`` | ``unchanged`` (formatting or
    comments only) | ``unsupported`` (no analysis possible; ``note`` says why).
    """

    path: str
    status: str
    note: str = ""
    changes: list[Change] = field(default_factory=list)


@dataclass
class ChangeSet:
    """Analysis of all changed files between two versions."""

    files: dict[str, FileChange] = field(default_factory=dict)

    def render(self, path: str) -> str:
        """Human/LLM-readable summary of one file's changes."""
        fc = self.files.get(path)
        if fc is None:
            return f"{path}\n  (no analysis available)"
        header = {
            "added": f"{path}  [new file]",
            "removed": f"{path}  [deleted]",
            "changed": path,
            "unchanged": f"{path}  [formatting or comments only - no behavioural change]",
            "unsupported": f"{path}  [no structural analysis: {fc.note} - rely on the diff]",
        }[fc.status]
        return "\n".join([header, *(f"  {c.render()}" for c in fc.changes)])

    def render_all(self, paths: list[str] | None = None) -> str:
        """Summary of several files (default: all), separated by blank lines."""
        return "\n\n".join(self.render(p) for p in (paths if paths is not None else sorted(self.files)))

    def public_api_files(self) -> list[str]:
        """Files whose public symbols were added, removed, renamed, moved or re-signed."""
        return sorted(
            fc.path
            for fc in self.files.values()
            if any(c.kind in API_KINDS and is_public(c.name) for c in fc.changes)
        )

    def count(self, kind: str) -> int:
        """Total number of changes of one kind across all files."""
        return sum(1 for fc in self.files.values() for c in fc.changes if c.kind == kind)


def _diff_symbols(
    before: ModuleInfo, after: ModuleInfo, fc: FileChange
) -> tuple[list[Symbol], list[Symbol]]:
    """Record signature/body changes in ``fc``; return (added, removed) symbols."""
    added: list[Symbol] = []
    for qual, new_sym in after.symbols.items():
        old_sym = before.symbols.get(qual)
        if old_sym is None:
            added.append(new_sym)
        elif old_sym.signature != new_sym.signature:
            also = " (implementation changed too)" if old_sym.body_hash != new_sym.body_hash else ""
            fc.changes.append(
                Change("signature", qual, f"{old_sym.signature} => {new_sym.signature}{also}")
            )
        elif old_sym.body_hash != new_sym.body_hash:
            what = (
                "class attributes or fields changed"
                if new_sym.kind == "class"
                else "implementation changed, signature unchanged"
            )
            fc.changes.append(Change("body", qual, what))
    removed = [s for q, s in before.symbols.items() if q not in after.symbols]
    return added, removed


def _match_renames(
    added: list[tuple[str, Symbol]], removed: list[tuple[str, Symbol]]
) -> list[tuple[int, int]]:
    """Pair removed with added functions that have identical, non-trivial bodies.

    Only unambiguous 1:1 matches are returned (indices into ``removed``, ``added``).
    """

    def eligible(sym: Symbol) -> bool:
        return sym.kind != "class" and bool(sym.body_hash) and sym.weight >= MIN_RENAME_WEIGHT

    added_by_hash: dict[str, list[int]] = defaultdict(list)
    removed_by_hash: dict[str, list[int]] = defaultdict(list)
    for i, (_, sym) in enumerate(added):
        if eligible(sym):
            added_by_hash[sym.body_hash].append(i)
    for j, (_, sym) in enumerate(removed):
        if eligible(sym):
            removed_by_hash[sym.body_hash].append(j)
    return [
        (removed_by_hash[h][0], idxs[0])
        for h, idxs in added_by_hash.items()
        if len(idxs) == 1 and len(removed_by_hash.get(h, [])) == 1
    ]


def analyze_changes(old: dict[str, str], new: dict[str, str]) -> ChangeSet:
    """Compare two snapshots of a set of files.

    Args:
        old: ``{path: source}`` before; a missing key means the file did not exist.
        new: ``{path: source}`` after; a missing key means the file was deleted.

    Renames and moves are detected across *all* files, so a function that was
    moved from ``a.py`` to ``b.py`` shows up as ``moved out`` / ``moved in``
    instead of an unrelated removal and addition.
    """
    cs = ChangeSet()
    pending: dict[str, tuple[list[Symbol], list[Symbol]]] = {}

    for path in sorted(old.keys() | new.keys()):
        before = parse_module(path, old[path]) if path in old else None
        after = parse_module(path, new[path]) if path in new else None

        unparsed = next((m for m in (before, after) if m is not None and not m.parsed), None)
        if unparsed is not None:
            cs.files[path] = FileChange(path, "unsupported", unparsed.reason)
            continue

        b = before or ModuleInfo(path)
        a = after or ModuleInfo(path)
        status = "added" if before is None else "removed" if after is None else "changed"
        if status == "changed" and b.module_hash == a.module_hash:
            cs.files[path] = FileChange(path, "unchanged", "formatting or comments only")
            continue

        fc = FileChange(path, status)
        cs.files[path] = fc
        pending[path] = _diff_symbols(b, a, fc)

        old_imports, new_imports = set(b.imports), set(a.imports)
        for imp in sorted(new_imports - old_imports, key=ImportRef.render):
            fc.changes.append(Change("import_added", imp.render()))
        for imp in sorted(old_imports - new_imports, key=ImportRef.render):
            fc.changes.append(Change("import_removed", imp.render()))

        added_syms, removed_syms = pending[path]
        nothing_else_found = not (fc.changes or added_syms or removed_syms)
        if a.rest_hash != b.rest_hash or nothing_else_found:
            fc.changes.append(
                Change("other", "module", "module-level code, constants or docstring changed")
            )

    # Cross-file rename/move detection on the symbols that were added/removed.
    added = [(p, s) for p, (ad, _) in pending.items() for s in ad]
    removed = [(p, s) for p, (_, rm) in pending.items() for s in rm]
    used_added: set[int] = set()
    used_removed: set[int] = set()
    for j, i in _match_renames(added, removed):
        (old_path, old_sym), (new_path, new_sym) = removed[j], added[i]
        used_removed.add(j)
        used_added.add(i)
        if old_path == new_path:
            cs.files[new_path].changes.append(Change("renamed", new_sym.qualname, f"was {old_sym.qualname}"))
        else:
            cs.files[new_path].changes.append(
                Change("moved_in", new_sym.qualname, f"was {old_path}::{old_sym.qualname}")
            )
            cs.files[old_path].changes.append(
                Change("moved_out", old_sym.qualname, f"now {new_path}::{new_sym.qualname}")
            )
    for i, (path, sym) in enumerate(added):
        if i not in used_added:
            cs.files[path].changes.append(Change("added", sym.qualname, sym.signature))
    for j, (path, sym) in enumerate(removed):
        if j not in used_removed:
            cs.files[path].changes.append(Change("removed", sym.qualname, sym.signature))
    return cs


# =============================================================================
# Whole project
# =============================================================================


def module_name(path: str) -> str:
    """Dotted module name of a path (``src/pkg/mod.py`` -> ``pkg.mod``; ``__init__`` -> package)."""
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _resolve_import(imp: ImportRef, package: list[str], index: dict[str, str]) -> set[str]:
    """Map an import to the project file(s) it refers to (empty for third-party)."""
    if imp.level:
        keep = len(package) - (imp.level - 1)
        if keep < 0:
            return set()
        base = package[:keep]
    else:
        base = []
    target = base + (imp.module.split(".") if imp.module else [])
    target_name = ".".join(target)
    if not imp.name:  # plain "import a.b"
        return {index[target_name]} if target_name in index else set()
    submodule = ".".join(target + [imp.name])
    if submodule in index:  # "from pkg import module"
        return {index[submodule]}
    if target_name in index:  # "from module import symbol"
        return {index[target_name]}
    return set()


@dataclass
class ProjectModel:
    """Structure of all analysable modules of a project at one commit."""

    modules: dict[str, ModuleInfo]

    def dependencies(self) -> dict[str, set[str]]:
        """``{path: {paths it imports}}`` - project-internal imports only."""
        index = {module_name(p): p for p in self.modules}
        deps: dict[str, set[str]] = {}
        for path, info in self.modules.items():
            name = module_name(path)
            parts = name.split(".") if name else []
            package = parts if PurePosixPath(path).name == "__init__.py" else parts[:-1]
            found: set[str] = set()
            for imp in info.imports:
                found |= _resolve_import(imp, package, index)
            deps[path] = found - {path}
        return deps

    def entry_points(self) -> list[str]:
        """Modules that no other module imports (CLI, scripts, top of the call graph)."""
        deps = self.dependencies()
        imported = set().union(*deps.values()) if deps else set()
        return sorted(
            p for p in self.modules if p not in imported and PurePosixPath(p).name != "__init__.py"
        )

    def fingerprint(self) -> str:
        """Hash of the *public structure*: modules, public signatures, internal imports.

        Implementation-only changes do not alter it, so the architecture note is
        only regenerated when the architecture can actually have changed.
        """
        deps = self.dependencies()
        data = [
            [
                path,
                self.modules[path].doc,
                sorted(s.signature for s in self.modules[path].symbols.values() if s.is_public),
                sorted(deps[path]),
            ]
            for path in sorted(self.modules)
        ]
        return _sha(json.dumps(data))

    def render_outline(self, max_chars: int = 20000) -> str:
        """Compact overview for the LLM: per module its docstring, imports, public API.

        If the result exceeds ``max_chars``, docstring summaries are dropped
        first, then the text is cut.
        """
        deps = self.dependencies()

        def build(with_docs: bool) -> str:
            blocks = []
            for path in sorted(self.modules):
                info = self.modules[path]
                lines = [f"## {path}"]
                if with_docs and info.doc:
                    lines.append(info.doc)
                if deps[path]:
                    lines.append("imports: " + ", ".join(sorted(deps[path])))
                for sym in info.symbols.values():
                    if sym.is_public:
                        doc = f" - {sym.summary}" if with_docs and sym.summary else ""
                        lines.append(f"{'  ' * sym.qualname.count('.')}- {sym.signature}{doc}")
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)

        for with_docs in (True, False):
            text = build(with_docs)
            if len(text) <= max_chars:
                return text
        return text[:max_chars] + "\n... (outline truncated)"

    def render_mermaid(self) -> str:
        """Mermaid ``flowchart`` of the internal import graph (drawn without the LLM)."""
        deps = self.dependencies()
        paths = sorted(self.modules)
        ids = {p: f"m{i}" for i, p in enumerate(paths)}
        lines = ["flowchart LR"]
        lines += [f'    {ids[p]}["{module_name(p) or p}"]' for p in paths]
        lines += [f"    {ids[p]} --> {ids[d]}" for p in paths for d in sorted(deps[p])]
        return "\n".join(lines)


def build_project_model(sources: dict[str, str]) -> ProjectModel:
    """Build the model from ``{path: source}``; files that are not analysable are skipped."""
    modules = {}
    for path, source in sources.items():
        info = parse_module(path, source)
        if info.parsed:
            modules[path] = info
    return ProjectModel(modules)
