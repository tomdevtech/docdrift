"""Tests for docdrift.playbook_loader."""

from pathlib import Path

from docdrift.playbook_loader import resolve_playbook_name


def test_resolve_playbook_name_python():
    assert resolve_playbook_name(Path("src/module.py")) == "python"


def test_resolve_playbook_name_typescript():
    assert resolve_playbook_name(Path("src/Component.tsx")) == "typescript"


def test_resolve_playbook_name_fallback():
    assert resolve_playbook_name(Path("README.md")) == "fallback"
