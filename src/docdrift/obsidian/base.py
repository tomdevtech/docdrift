"""Shared interface and helpers for all Obsidian writers.

Each implementation (local_writer.py and rest_api_writer.py) satisfies this
protocol. This keeps the rest of docdrift independent of whether the final
operation uses file-system access or an HTTP call.
"""

from pathlib import Path
from typing import Protocol

AUTO_DOC_MARKER = "<!-- AUTO-DOC-END -->"


class ObsidianWriter(Protocol):
    """Interface implemented by local_writer.py and rest_api_writer.py."""

    def read_note(self, note_path: str) -> tuple[dict, str]:
        """Read the front matter and body of an existing note.

        Returns (frontmatter_dict, body_markdown). Return an empty dict and
        string if the note does not exist; this is an expected case.
        """
        ...

    def write_note(self, note_path: str, frontmatter: dict, generated_body: str) -> None:
        """Write the note while preserving its manual section."""
        ...


def resolve_note_path(repo_name: str, source_file: Path) -> str:
    """Map a repository file path to a relative vault path.

    Example: repo_name="my-project", source_file=Path("src/module.py")
    -> "Projects/my-project/src/module.md"
    """
    note_relative = source_file.with_suffix(".md")
    return (Path("Projects") / repo_name / note_relative).as_posix()


def merge_with_manual_section(existing_body: str, generated_body: str) -> str:
    """Append an existing manual section after AUTO_DOC_MARKER.

    This preserves the section when generated content is overwritten. Both
    writer implementations use this helper.
    """
    manual_part = ""
    if existing_body and AUTO_DOC_MARKER in existing_body:
        manual_part = existing_body.split(AUTO_DOC_MARKER, 1)[1]

    return f"{generated_body.rstrip()}\n\n{AUTO_DOC_MARKER}{manual_part}"
