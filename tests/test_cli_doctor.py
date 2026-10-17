"""Tests for the pre-push hook installation and `docdrift check`."""

import os
import shutil
import subprocess
import time

import pytest
from click.testing import CliRunner

from docdrift.cli import commands as cli
from docdrift.setup import doctor


def _bash() -> str:
    """Git for Windows' bash; plain `bash` on Windows may be the WSL launcher, which fails without a distro."""
    git = shutil.which("git")
    if os.name == "nt" and git:
        candidate = os.path.join(os.path.dirname(os.path.dirname(git)), "bin", "bash.exe")
        if os.path.exists(candidate):
            return candidate
    return shutil.which("bash") or "bash"


@pytest.fixture
def initialised(repo, monkeypatch):
    monkeypatch.chdir(repo.root)
    result = CliRunner().invoke(cli.main, ["init"])
    assert result.exit_code == 0, result.output
    return repo


def test_init_writes_an_lf_hook_with_the_interpreter_path(initialised):
    hook = initialised.root / ".git" / "hooks" / "pre-push"
    raw = hook.read_bytes()
    assert b"\r\n" not in raw and b"__PYTHON__" not in raw
    assert b"-m docdrift.cli" in raw
    gitignore = (initialised.root / ".gitignore").read_text(encoding="utf-8")
    assert ".docdrift/state/" in gitignore and ".docdrift/logs/" in gitignore


def test_hook_skip_and_missing_python_never_block_the_push(initialised):
    hook = initialised.root / ".git" / "hooks" / "pre-push"
    run = subprocess.run([_bash(), str(hook)], env={"DOCDRIFT_SKIP": "1", "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, cwd=initialised.root, check=False)
    assert run.returncode == 0 and run.stdout == ""
    broken = hook.read_text(encoding="utf-8").replace('PYTHON="', 'PYTHON="/nonexistent/python', 1)
    hook.write_text(broken, encoding="utf-8", newline="\n")
    run = subprocess.run([_bash(), str(hook)], env={"PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, cwd=initialised.root, check=False)
    assert run.returncode == 0 and "not installed" in run.stdout


def test_hook_runs_in_the_background_and_logs(initialised):
    hook = initialised.root / ".git" / "hooks" / "pre-push"
    run = subprocess.run([_bash(), str(hook)], capture_output=True, text=True, cwd=initialised.root, timeout=30, check=False)
    assert run.returncode == 0 and "background" in run.stdout
    log = initialised.root / ".docdrift" / "logs" / "docdrift.log"
    for _ in range(100):
        if log.exists() and "=== exit" in log.read_text(encoding="utf-8"):
            break
        time.sleep(0.1)
    text = log.read_text(encoding="utf-8")
    assert "pre-push ===" in text and "=== exit" in text
    assert "No module named" not in text  # docdrift really started via the interpreter path


def test_check_reports_crlf_hook_and_projects_vault_path(initialised, tmp_path):
    hook = initialised.root / ".git" / "hooks" / "pre-push"
    hook.write_bytes(hook.read_bytes().replace(b"\n", b"\r\n"))
    vault = tmp_path / "Vault" / "Projects"
    vault.mkdir(parents=True)
    cfg = initialised.root / ".docdrift" / "config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('"~/ObsidianVault"', f'"{vault.as_posix()}"')
        .replace("http://localhost:11434/v1", "http://127.0.0.1:9/v1"),
        encoding="utf-8",
    )
    findings = doctor.run_checks(initialised.root, probe=False)
    text = {(f.level, f.topic, f.message.split(" ")[0]) for f in findings}
    messages = "\n".join(f"{f.level} {f.message}" for f in findings)
    assert "ends in 'Projects'" in messages
    assert "Windows line endings" in messages
    assert ("error", "llm", "server") in text
    result = CliRunner().invoke(cli.main, ["check", "--no-probe"])
    assert result.exit_code == 1 and "[FAIL]" in result.output


def test_status_shows_mode_vault_and_last_commit(initialised, tmp_path):
    vault = tmp_path / "Vault" / "Projects"
    vault.mkdir(parents=True)
    cfg = initialised.root / ".docdrift" / "config.yaml"
    cfg.write_text(
        cfg.read_text(encoding="utf-8").replace('"~/ObsidianVault"', f'"{vault.as_posix()}"')
        .replace('mode: "both"', 'mode: "overview"'),
        encoding="utf-8",
    )
    initialised.commit({"a.py": "x = 1\n"})
    result = CliRunner().invoke(cli.main, ["status"])
    assert result.exit_code == 0, result.output
    assert "mode        overview" in result.output
    assert str(tmp_path / "Vault" / "Projects" / initialised.root.name) in result.output
    assert "documented  never" in result.output and "_Architecture.md" in result.output


def test_run_dry_run_prints_the_plan_and_writes_nothing(initialised, tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    cfg = initialised.root / ".docdrift" / "config.yaml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace('"~/ObsidianVault"', f'"{vault.as_posix()}"'), encoding="utf-8")
    initialised.commit({"pkg/a.py": '"""A."""\ndef a():\n    return 1\n'})
    result = CliRunner().invoke(cli.main, ["run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "Would document 2 file(s):" in result.output  # pkg/a.py and the .gitignore from init
    assert "\n  pkg/a.py\n" in result.output
    assert "Architecture overview: would be regenerated" in result.output
    assert "Dry run - nothing was sent to the LLM" in result.output
    assert not any(vault.iterdir())
