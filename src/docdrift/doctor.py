"""``docdrift check``: verify the setup before relying on it.

Every check returns a :class:`Finding`; nothing is changed. Typical problems it
catches: the vault path points at ``Projects`` (notes would land in
``Projects/Projects/<repo>``), the LLM server is down or the configured model is
not loaded, the model's context is too small for ``max_prompt_chars``, or the
pre-push hook is missing, outdated or saved with Windows line endings.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import requests

from docdrift.config import DocdriftConfig, load_config
from docdrift.llm_client import ChatMessage, LLMClient


@dataclass
class Finding:
    level: str  # "ok" | "warn" | "error"
    topic: str
    message: str


def _config(repo_root: Path) -> tuple[DocdriftConfig | None, list[Finding]]:
    try:
        cfg = load_config(repo_root)
    except FileNotFoundError as exc:
        return None, [Finding("error", "config", str(exc))]
    except Exception as exc:  # noqa: BLE001 - YAML errors etc.
        return None, [Finding("error", "config", f"cannot be read: {exc}")]
    return cfg, [Finding("ok", "config", f"loaded (repo_name: {cfg.repo_name})")]


def _vault(cfg: DocdriftConfig) -> list[Finding]:
    obs = cfg.obsidian
    if obs.access_mode == "rest_api":
        if not obs.rest_api_base_url or not obs.rest_api_key:
            return [Finding("error", "vault", "rest_api_base_url / rest_api_key missing")]
        try:
            r = requests.get(
                f"{obs.rest_api_base_url.rstrip('/')}/",
                headers={"Authorization": f"Bearer {obs.rest_api_key}"},
                timeout=10, verify=obs.rest_api_verify,
            )
        except requests.exceptions.RequestException as exc:
            return [Finding("error", "vault", f"REST API not reachable: {exc}")]
        return [Finding("ok" if r.ok else "error", "vault", f"REST API answered {r.status_code}")]
    if obs.access_mode != "local":
        return [Finding("error", "vault", f"unknown access_mode {obs.access_mode!r}")]
    if not obs.vault_path:
        return [Finding("error", "vault", "obsidian.vault_path is missing")]
    out = []
    root = obs.vault_path
    if not root.is_dir():
        out.append(Finding("error", "vault", f"{root} does not exist"))
        return out
    if root.name.lower() == "projects":
        out.append(Finding(
            "warn", "vault",
            f"{root} ends in 'Projects' - notes would go to {root / 'Projects' / cfg.repo_name}. "
            "Point vault_path at the vault root.",
        ))
    if not (root / ".obsidian").is_dir():
        out.append(Finding("warn", "vault", f"{root} has no .obsidian folder - is it the vault root?"))
    target = root / "Projects" / cfg.repo_name
    out.append(Finding("ok", "vault", f"notes go to {target}"))
    return out


def _llm(cfg: DocdriftConfig, probe: bool) -> list[Finding]:
    base = cfg.api_base_url.rstrip("/")
    try:
        r = requests.get(f"{base}/models", timeout=10)
        r.raise_for_status()
        models = [m.get("id", "") for m in r.json().get("data", [])]
    except Exception as exc:  # noqa: BLE001
        return [Finding("error", "llm", f"server at {base} not reachable: {exc}")]
    out = [Finding("ok", "llm", f"server reachable ({len(models)} model(s))")]
    if models and cfg.model not in models:
        out.append(Finding(
            "error", "llm",
            f"model {cfg.model!r} not available; the server offers: {', '.join(models[:8])}",
        ))
        return out
    if probe:
        # A prompt of the configured size shows whether the context is large enough.
        filler = ("x = 1  # padding\n" * (cfg.max_prompt_chars // 17))[: cfg.max_prompt_chars]
        try:
            reply = LLMClient(cfg.api_base_url, cfg.model, timeout=300, retries=0).chat(
                [ChatMessage("user", f"{filler}\n\nIgnore the lines above. Reply with the word OK.")]
            )
            out.append(Finding("ok", "llm", f"model answers a {cfg.max_prompt_chars}-char prompt ({reply[:20]!r})"))
        except RuntimeError as exc:
            out.append(Finding("error", "llm", f"test prompt failed: {exc}"))
    return out


def _hook(repo_root: Path) -> list[Finding]:
    hook = repo_root / ".git" / "hooks" / "pre-push"
    if not hook.exists():
        return [Finding("warn", "hook", "no pre-push hook - run `docdrift init`")]
    raw = hook.read_bytes()
    if b"docdrift" not in raw:
        return [Finding("warn", "hook", "pre-push hook exists but is not docdrift's")]
    out = []
    if b"\r\n" in raw:
        out.append(Finding("error", "hook", "hook has Windows line endings and will not run - run `docdrift init`"))
    if b"__PYTHON__" in raw or b"DOCDRIFT_SYNC" not in raw:
        out.append(Finding("warn", "hook", "hook is outdated - run `docdrift init`"))
    return out or [Finding("ok", "hook", "pre-push hook installed")]


def run_checks(repo_root: Path, probe: bool = True) -> list[Finding]:
    """All checks for the repository at ``repo_root``."""
    cfg, findings = _config(repo_root)
    if cfg is not None:
        findings += _vault(cfg)
        findings += _llm(cfg, probe)
    findings += _hook(repo_root)
    return findings
