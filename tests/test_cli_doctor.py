"""Tests for the pre-push hook installation and `docdrift check`."""

import subprocess
import time

import pytest
from click.testing import CliRunner

from docdrift.cli import commands as cli
from docdrift.setup import doctor


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
    run = subprocess.run(["bash", str(hook)], env={"DOCDRIFT_SKIP": "1", "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, cwd=initialised.root, check=False)
    assert run.returncode == 0 and run.stdout == ""
    broken = hook.read_text(encoding="utf-8").replace('PYTHON="', 'PYTHON="/nonexistent/python', 1)
    hook.write_text(broken, encoding="utf-8", newline="\n")
    run = subprocess.run(["bash", str(hook)], env={"PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, cwd=initialised.root, check=False)
    assert run.returncode == 0 and "not installed" in run.stdout


def test_hook_runs_in_the_background_and_logs(initialised):
    hook = initialised.root / ".git" / "hooks" / "pre-push"
    run = subprocess.run(["bash", str(hook)], capture_output=True, text=True, cwd=initialised.root, timeout=30, check=False)
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
