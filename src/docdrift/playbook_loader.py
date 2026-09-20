"""Select and load the appropriate playbook (system prompt) for a file.

Lookup order (first match wins):
    1. Project-specific playbook in .docdrift/playbooks/<name>.md
    2. Bundled default playbook in the docdrift package
"""

import importlib.resources
from pathlib import Path

EXTENSION_MAP = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "typescript",
}


def resolve_playbook_name(file_path: Path) -> str:
    """Determine the appropriate playbook from the file extension."""
    return EXTENSION_MAP.get(file_path.suffix, "fallback")


def _read_playbook(name: str, project_playbook_dir: Path | None) -> str:
    filename = f"{name}.md"

    if project_playbook_dir is not None:
        override = project_playbook_dir / filename
        if override.exists():
            return override.read_text(encoding="utf-8")

    # Use importlib.resources.files("docdrift"), not ("docdrift.playbooks"),
    # because playbooks/ is a data directory inside docdrift rather than a
    # Python subpackage with an __init__.py. This works for both regular pip
    # installations and editable installs.
    package_root = importlib.resources.files("docdrift")
    return package_root.joinpath("playbooks", filename).read_text(encoding="utf-8")


def load_playbook(name: str, project_playbook_dir: Path | None = None) -> str:
    """Load the playbook contents, prepending base.md."""
    base = _read_playbook("base", project_playbook_dir)
    if name == "base":
        return base

    specific = _read_playbook(name, project_playbook_dir)
    return f"{base}\n\n{specific}"
