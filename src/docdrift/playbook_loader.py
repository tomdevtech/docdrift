"""Select and load playbooks (system prompts).

Per-file documentation uses ``base.md`` plus a language playbook. The two
project-wide stages use a standalone playbook each (``architecture.md`` and
``adr.md``).

Lookup order for every playbook (first match wins):

1. project-specific override in ``<playbook_dir>/<name>.md`` (config: ``playbook_dir``)
2. bundled default in the docdrift package
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
}

# Playbooks that are used on their own, without ``base.md`` prepended.
STANDALONE = {"architecture", "adr"}


def resolve_playbook_name(file_path: Path) -> str:
    """Pick the language playbook from the file extension (``fallback`` if unknown)."""
    return EXTENSION_MAP.get(file_path.suffix, "fallback")


def _read_playbook(name: str, project_playbook_dir: Path | None) -> str:
    filename = f"{name}.md"
    if project_playbook_dir is not None:
        override = project_playbook_dir / filename
        if override.exists():
            return override.read_text(encoding="utf-8")
    # playbooks/ is a data directory (no __init__.py), hence files("docdrift").
    return importlib.resources.files("docdrift").joinpath("playbooks", filename).read_text(
        encoding="utf-8"
    )


def load_playbook(
    name: str, project_playbook_dir: Path | None = None, language: str = "English"
) -> str:
    """Return the system prompt for ``name``.

    Language playbooks are prefixed with ``base.md``; ``architecture`` and
    ``adr`` are returned as they are. For a ``language`` other than English an
    instruction is appended (section names stay English so notes stay uniform).
    """
    if name in STANDALONE or name == "base":
        text = _read_playbook(name, project_playbook_dir)
    else:
        base = _read_playbook("base", project_playbook_dir)
        text = f"{base}\n\n{_read_playbook(name, project_playbook_dir)}"
    if language and language.strip().lower() != "english":
        text += (
            f"\n\n## Language\n\nWrite all prose in {language.strip()}. Keep the section "
            "headings, the `Title:` line, code identifiers and signatures exactly as specified."
        )
    return text
