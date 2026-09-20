"""Read and write notes in an Obsidian vault.

Notes may contain a manually maintained section below a marker such as
"<!-- AUTO-DOC-END -->". This section must be preserved when overwriting a
note.
"""

from pathlib import Path

AUTO_DOC_MARKER = "<!-- AUTO-DOC-END -->"


def resolve_note_path(vault_path: Path, repo_name: str, source_file: Path) -> Path:
    """Map a repository path to a path in the vault.

    TODO: For example, <vault>/Projects/<repo>/<source_file with .md replacing
    its original extension>. Handle path separators if source_file is deeply
    nested.
    """
    raise NotImplementedError


def read_existing_note(note_path: Path) -> tuple[dict, str]:
    """Read the front matter and body of an existing note, if present.

    Returns (frontmatter_dict, body_markdown).
    TODO: Use python-frontmatter; return an empty dict/string if absent.
    """
    raise NotImplementedError


def write_note(note_path: Path, frontmatter: dict, generated_body: str) -> None:
    """Write the note while preserving the manual section after AUTO_DOC_MARKER.

    TODO:
        - Extract the existing manual section, if present
        - Combine generated_body, AUTO_DOC_MARKER, and the manual section
        - Write atomically using a temporary file followed by os.replace()
        - Create the target directory if needed (parents=True)
    """
    raise NotImplementedError
