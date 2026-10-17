"""Tests for docdrift.local.vault (local file access and mocked REST API)."""

from pathlib import Path

import pytest
import requests

from docdrift.local import vault as vault_module
from docdrift.local.vault import (
    AUTO_DOC_MARKER,
    LocalVault,
    RestVault,
    merge_with_manual_section,
    resolve_note_path,
)


def test_note_paths():
    assert resolve_note_path("proj", Path("src/mod.py")) == "Projects/proj/src/mod.md"
    assert vault_module.architecture_note_path("proj") == "Projects/proj/_Architecture.md"


def test_merge_keeps_manual_section_and_drops_marker_written_by_llm():
    existing = f"old\n\n{AUTO_DOC_MARKER}\nmy notes"
    merged = merge_with_manual_section(existing, f"new text\n{AUTO_DOC_MARKER}\nLLM garbage")
    assert merged == f"new text\n\n{AUTO_DOC_MARKER}\nmy notes"


def test_local_write_creates_file_with_front_matter(tmp_path):
    v = LocalVault(tmp_path)
    v.write_note("Projects/p/a.md", {"project": "p"}, "## Purpose\nx")
    meta, body = v.read_note("Projects/p/a.md")
    assert meta == {"project": "p"} and AUTO_DOC_MARKER in body


def test_local_write_preserves_manual_section(tmp_path):
    v = LocalVault(tmp_path)
    v.write_note("n.md", {}, "v1")
    path = tmp_path / "n.md"
    path.write_text(path.read_text(encoding="utf-8") + "\nMY OWN NOTE", encoding="utf-8")
    v.write_note("n.md", {}, "v2")
    _, body = v.read_note("n.md")
    assert body.startswith("v2") and "MY OWN NOTE" in body


def test_local_read_missing_note_is_empty(tmp_path):
    assert LocalVault(tmp_path).read_note("nope.md") == ({}, "")


class _Resp:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def test_rest_read_404_is_empty(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(404))
    assert RestVault("http://x", "key").read_note("a.md") == ({}, "")


def test_rest_write_sends_put_with_auth(monkeypatch):
    sent = {}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(404))

    def fake_put(url, data, headers, timeout, verify=True):
        sent.update(url=url, data=data.decode(), headers=headers)
        return _Resp()

    monkeypatch.setattr(requests, "put", fake_put)
    RestVault("http://x/", "key").write_note("Projects/p/a.md", {"project": "p"}, "body")
    assert sent["url"] == "http://x/vault/Projects/p/a.md"
    assert sent["headers"]["Authorization"] == "Bearer key"
    assert "project: p" in sent["data"] and AUTO_DOC_MARKER in sent["data"]


def test_rest_connection_error_is_explained(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.ConnectionError()

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(RuntimeError, match="Obsidian running"):
        RestVault("http://x", "k").read_note("a.md")


def test_rest_paths_are_url_encoded_and_tls_setting_is_passed(monkeypatch):
    seen = {}

    def fake_get(url, headers, timeout, verify):
        seen.update(url=url, verify=verify)
        return _Resp(404)

    monkeypatch.setattr(requests, "get", fake_get)
    RestVault("https://x:27124", "k", verify="/certs/obsidian.crt").read_note("Projects/p/Mein Ordner/ä.md")
    assert seen["url"] == "https://x:27124/vault/Projects/p/Mein%20Ordner/%C3%A4.md"
    assert seen["verify"] == "/certs/obsidian.crt"


def test_notes_are_written_with_lf(tmp_path):
    LocalVault(tmp_path).write_note("a.md", {"p": 1}, "line1\nline2")
    assert b"\r\n" not in (tmp_path / "a.md").read_bytes()


def test_vault_path_pointing_at_projects_uses_its_parent(tmp_path):
    from docdrift.core.config import DocdriftConfig, GithubDocsConfig, ObsidianConfig

    (tmp_path / "Projects").mkdir()
    cfg = DocdriftConfig(
        repo_name="p", api_base_url="", model="", github_docs=GithubDocsConfig(),
        obsidian=ObsidianConfig("local", vault_path=tmp_path / "Projects"),
    )
    v = vault_module.open_vault(cfg)
    assert v.root == tmp_path
    assert vault_module.vault_root(tmp_path / "Vault") == tmp_path / "Vault"
