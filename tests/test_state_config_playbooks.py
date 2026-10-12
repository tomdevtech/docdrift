"""Tests for docdrift.state, docdrift.config and docdrift.playbook_loader."""

from pathlib import Path

from docdrift import state
from docdrift.config import load_config, write_default_config
from docdrift.playbook_loader import load_playbook, resolve_playbook_name


def test_state_roundtrip_and_empty_default(tmp_path):
    assert state.load_state(tmp_path, "p") == state.State()
    st = state.State(last_sha="abc", fingerprint="f", adrs=[{"number": 1}])
    state.save_state(tmp_path, "p", st)
    assert state.load_state(tmp_path, "p") == st


def test_state_reads_legacy_last_sha_file(tmp_path):
    (tmp_path / "p-last-sha").write_text("deadbeef\n", encoding="utf-8")
    assert state.load_state(tmp_path, "p").last_sha == "deadbeef"


def test_default_config_is_loadable_and_has_new_defaults(tmp_path):
    write_default_config(tmp_path)
    cfg = load_config(tmp_path)
    assert cfg.repo_name == tmp_path.name
    assert cfg.adr.enabled and cfg.adr.threshold == 4
    assert cfg.github_docs.enabled and cfg.github_docs.precheck and cfg.github_docs.assignee == "copilot"
    assert cfg.architecture_note and cfg.max_prompt_chars == 12000


def test_old_config_without_new_keys_still_loads(tmp_path):
    (tmp_path / ".docdrift").mkdir()
    (tmp_path / ".docdrift" / "config.yaml").write_text('repo_name: "x"\nobsidian:\n  access_mode: "local"\n', encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.adr.threshold == 4 and cfg.architecture_note


def test_obsolete_adr_location_is_ignored(tmp_path):
    """Configs written by the 0.2 pre-release may still contain adr.location."""
    (tmp_path / ".docdrift").mkdir()
    (tmp_path / ".docdrift" / "config.yaml").write_text("adr:\n  location: repo\n  threshold: 6\n", encoding="utf-8")
    cfg = load_config(tmp_path)
    assert cfg.adr.threshold == 6 and not hasattr(cfg.adr, "location")


def test_env_placeholder_is_resolved(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_KEY", "secret")
    (tmp_path / ".docdrift").mkdir()
    (tmp_path / ".docdrift" / "config.yaml").write_text('obsidian:\n  rest_api_key: "${MY_KEY}"\n', encoding="utf-8")
    assert load_config(tmp_path).obsidian.rest_api_key == "secret"


def test_playbook_names():
    assert resolve_playbook_name(Path("a.py")) == "python"
    assert resolve_playbook_name(Path("a.tsx")) == "typescript"
    assert resolve_playbook_name(Path("README.md")) == "fallback"


def test_language_playbooks_include_base_standalone_ones_do_not():
    python = load_playbook("python")
    assert "structural analysis" in python and "Language-specific guidance: Python" in python
    architecture = load_playbook("architecture")
    assert "architecture (HLA)" in architecture and "Language-specific" not in architecture
    assert load_playbook("adr").startswith("You draft an Architecture Decision Record")


def test_project_playbook_overrides_bundled(tmp_path):
    (tmp_path / "python.md").write_text("MY RULES", encoding="utf-8")
    assert load_playbook("python", tmp_path).endswith("MY RULES")


def test_language_instruction_is_appended_but_headings_stay():
    assert "Write all prose in" not in load_playbook("python")
    german = load_playbook("python", language="German")
    assert german.endswith("exactly as specified.") and "Write all prose in German" in german
    assert "## Purpose" in german  # section names unchanged
    assert "Write all prose in German" in load_playbook("adr", language="German")


def test_language_is_read_from_config(tmp_path):
    (tmp_path / ".docdrift").mkdir()
    (tmp_path / ".docdrift" / "config.yaml").write_text('language: "German"\n', encoding="utf-8")
    assert load_config(tmp_path).language == "German"
