"""Read and write notes in an Obsidian vault.

Two interchangeable implementations share the same two methods
(:class:`Vault` protocol); :func:`open_vault` picks one from the config:

* :class:`LocalVault`  - direct file access. Works while Obsidian is closed, but
  the process must run on the same machine as the vault.
* :class:`RestVault`   - Obsidian's "Local REST API" plugin over HTTP. Works from
  anywhere (e.g. a GitHub-hosted runner) but needs Obsidian to be open and a
  tunnel such as Tailscale. See ``docs/obsidian-rest-api-setup.md``.

Every note ends with :data:`AUTO_DOC_MARKER`. Everything *below* the marker is
owned by the user and survives regeneration (:func:`merge_with_manual_section`).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

import frontmatter
import requests

from docdrift.core.config import DocdriftConfig

AUTO_DOC_MARKER = "<!-- AUTO-DOC-END -->"


class Vault(Protocol):
    """Interface of both vault implementations."""

    def read_note(self, note_path: str) -> tuple[dict, str]:
        """Return ``(front matter, body)``; ``({}, "")`` if the note does not exist."""
        ...

    def write_note(self, note_path: str, metadata: dict, generated_body: str) -> None:
        """Write a note, keeping the manual section below :data:`AUTO_DOC_MARKER`."""
        ...


# --- note paths ---------------------------------------------------------------


def resolve_note_path(repo_name: str, source_file: Path) -> str:
    """Map a repository file to its vault path.

    ``("my-project", "src/module.py")`` -> ``"Projects/my-project/src/module.md"``
    """
    return (Path("Projects") / repo_name / source_file.with_suffix(".md")).as_posix()


def architecture_note_path(repo_name: str) -> str:
    """Path of the project-wide architecture note (sorts first in the folder)."""
    return f"Projects/{repo_name}/_Architecture.md"


def adr_note_path(repo_name: str, filename: str) -> str:
    """Path of an ADR note, e.g. ``Projects/<repo>/ADR/0001-use-ast.md``."""
    return f"Projects/{repo_name}/ADR/{filename}"


# --- shared helper ------------------------------------------------------------


def merge_with_manual_section(existing_body: str, generated_body: str) -> str:
    """Combine freshly generated text with the user's manual section.

    Anything the LLM wrote after a marker is discarded; the manual section is
    taken from the existing note (everything after its marker).
    """
    generated = generated_body.split(AUTO_DOC_MARKER, 1)[0].rstrip()
    manual = ""
    if existing_body and AUTO_DOC_MARKER in existing_body:
        manual = existing_body.split(AUTO_DOC_MARKER, 1)[1]
    return f"{generated}\n\n{AUTO_DOC_MARKER}{manual}"


def _dump(metadata: dict, body: str) -> str:
    post = frontmatter.Post(body)
    post.metadata.update(metadata)
    return frontmatter.dumps(post)


# --- implementations ----------------------------------------------------------


class LocalVault:
    """Vault access through the file system."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def read_note(self, note_path: str) -> tuple[dict, str]:
        full = self.root / note_path
        if not full.exists():
            return {}, ""
        post = frontmatter.loads(full.read_text(encoding="utf-8"))
        return dict(post.metadata), post.content

    def write_note(self, note_path: str, metadata: dict, generated_body: str) -> None:
        full = self.root / note_path
        full.parent.mkdir(parents=True, exist_ok=True)
        _, existing = self.read_note(note_path)
        text = _dump(metadata, merge_with_manual_section(existing, generated_body))
        # Atomic write: Obsidian never sees a half-written file.
        tmp = full.with_suffix(full.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, full)


class RestVault:
    """Vault access through Obsidian's Local REST API plugin."""

    def __init__(
        self, base_url: str, api_key: str, timeout: int = 30, verify: bool | str = True
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.verify = verify  # False or a certificate path for the plugin's self-signed cert

    def _url(self, note_path: str) -> str:
        return f"{self.base_url}/vault/{quote(note_path)}"

    def _headers(self, **extra: str) -> dict:
        return {"Authorization": f"Bearer {self.api_key}", **extra}

    def read_note(self, note_path: str) -> tuple[dict, str]:
        try:
            response = requests.get(
                self._url(note_path), headers=self._headers(), timeout=self.timeout,
                verify=self.verify,
            )
        except requests.exceptions.ConnectionError as exc:
            raise RuntimeError(
                f"The Obsidian REST API at {self.base_url} is unavailable. "
                "Is Obsidian running, and is the tunnel active?"
            ) from exc
        if response.status_code == 404:
            return {}, ""
        response.raise_for_status()
        post = frontmatter.loads(response.text)
        return dict(post.metadata), post.content

    def write_note(self, note_path: str, metadata: dict, generated_body: str) -> None:
        _, existing = self.read_note(note_path)
        text = _dump(metadata, merge_with_manual_section(existing, generated_body))
        response = requests.put(
            self._url(note_path),
            data=text.encode("utf-8"),
            headers=self._headers(**{"Content-Type": "text/markdown"}),
            timeout=self.timeout,
            verify=self.verify,
        )
        response.raise_for_status()


def open_vault(config: DocdriftConfig) -> Vault:
    """Return the vault implementation selected by ``obsidian.access_mode``."""
    obs = config.obsidian
    if obs.access_mode == "local":
        if not obs.vault_path:
            raise ValueError("obsidian.vault_path is missing (access_mode: local).")
        if not obs.vault_path.is_dir():
            # Never create a vault: a typo would silently scatter notes on the disk.
            raise ValueError(f"obsidian.vault_path {obs.vault_path} does not exist.")
        return LocalVault(obs.vault_path)
    if obs.access_mode == "rest_api":
        if not obs.rest_api_base_url or not obs.rest_api_key:
            raise ValueError(
                "obsidian.rest_api_base_url / rest_api_key are missing (access_mode: rest_api)."
            )
        return RestVault(obs.rest_api_base_url, obs.rest_api_key, verify=obs.rest_api_verify)
    raise ValueError(f"Unknown obsidian.access_mode: {obs.access_mode!r} ('local' or 'rest_api')")
