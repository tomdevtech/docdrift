"""Access Obsidian directly through the file system.

The process (Git hook or self-hosted GitHub Actions runner) must run on the
same machine as the vault directory.
"""

import os
from pathlib import Path

import frontmatter

from docdrift.obsidian.base import merge_with_manual_section


class LocalObsidianWriter:
    """Implement ObsidianWriter (base.py) through direct file access."""

    def __init__(self, vault_path: Path) -> None:
        self.vault_path = vault_path

    def _resolve(self, note_path: str) -> Path:
        return self.vault_path / note_path

    def read_note(self, note_path: str) -> tuple[dict, str]:
        full_path = self._resolve(note_path)
        if not full_path.exists():
            return {}, ""

        post = frontmatter.loads(full_path.read_text(encoding="utf-8"))
        return dict(post.metadata), post.content

    def write_note(self, note_path: str, frontmatter_data: dict, generated_body: str) -> None:
        full_path = self._resolve(note_path)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        _, existing_body = self.read_note(note_path)
        merged_body = merge_with_manual_section(existing_body, generated_body)

        post = frontmatter.Post(content=merged_body, **frontmatter_data)
        text = frontmatter.dumps(post)

        # Write atomically: create a temporary file in the same directory, then
        # call os.replace(). This prevents Obsidian from reading a partial file.
        tmp_path = full_path.with_suffix(full_path.suffix + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, full_path)
