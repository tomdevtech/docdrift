"""Access Obsidian through the "Local REST API" community plugin.

Use this when the writing process does not run on the same machine as
Obsidian, for example with a GitHub-hosted Actions runner. By default, the
endpoint is available only locally (127.0.0.1:27124), so it must be exposed
through a tunnel such as Tailscale. See docs/obsidian-rest-api-setup.md.

Obsidian must be open and running.
"""

import frontmatter
import requests

from docdrift.obsidian.base import merge_with_manual_section


class RestApiObsidianWriter:
    """Implement ObsidianWriter (base.py) via the REST API plugin over HTTP."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"}

    def read_note(self, note_path: str) -> tuple[dict, str]:
        url = f"{self.base_url}/vault/{note_path}"
        try:
            response = requests.get(url, headers=self._headers(), timeout=self.timeout)
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

    def write_note(self, note_path: str, frontmatter_data: dict, generated_body: str) -> None:
        _, existing_body = self.read_note(note_path)
        merged_body = merge_with_manual_section(existing_body, generated_body)

        post = frontmatter.Post(content=merged_body, **frontmatter_data)
        text = frontmatter.dumps(post)

        url = f"{self.base_url}/vault/{note_path}"
        headers = {**self._headers(), "Content-Type": "text/markdown"}
        response = requests.put(
            url, data=text.encode("utf-8"), headers=headers, timeout=self.timeout
        )
        response.raise_for_status()
