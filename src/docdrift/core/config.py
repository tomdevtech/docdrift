"""Load ``.docdrift/config.yaml`` of the target project.

See ``examples/docdrift.core.config.example.yaml`` for the full schema. Every key
except ``obsidian`` is optional; missing keys fall back to the defaults below.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_RELATIVE_PATH = Path(".docdrift") / "config.yaml"

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")

#: What the local flow writes: ``per-file`` notes, the ``overview`` (HLA) note, or ``both``.
MODES = ("both", "overview", "per-file")


@dataclass
class ObsidianConfig:
    """How to reach the Obsidian vault (see :mod:`docdrift.local.vault`)."""

    access_mode: str  # "local" | "rest_api"
    vault_path: Path | None = None
    rest_api_base_url: str | None = None
    rest_api_key: str | None = None
    #: TLS check for the REST API: ``true``, ``false`` (the plugin's self-signed
    #: certificate) or the path to that certificate (recommended).
    rest_api_verify: bool | str = True


@dataclass
class GithubDocsConfig:
    """Merge flow: keep the product docs (README, ``docs_paths``) up to date.

    Copilot writes product docs only; technical docs are the local flow's job.

    After every merge into ``main`` docdrift checks whether the change could have
    made the product docs outdated (:mod:`docdrift.merge.docs_check`) and, if so, asks
    the Copilot coding agent to verify and update them in a pull request.

    ``enabled``: product-docs task on/off. ``precheck``: skip merges that cannot
    affect users (``false`` asks Copilot after every merge). ``assignee``:
    ``copilot`` (the coding agent) or any GitHub login.
    """

    enabled: bool = True
    precheck: bool = True
    assignee: str = "copilot"
    docs_paths: list[str] = field(default_factory=lambda: ["docs/", "README.md"])


@dataclass
class AdrConfig:
    """Architecture Decision Records - technical docs, local flow only.

    Drafted by the local LLM into the vault (``Projects/<repo>/ADR/``) when a
    change scores at least ``threshold`` (see :func:`docdrift.local.adr.assess`).
    The merge flow (Copilot) never writes ADRs.
    """

    enabled: bool = True
    threshold: int = 4


@dataclass
class DocdriftConfig:
    """Parsed configuration of a target project."""

    repo_name: str
    api_base_url: str
    model: str
    obsidian: ObsidianConfig
    github_docs: GithubDocsConfig
    playbook_dir: Path | None = None
    ignore: list[str] = field(default_factory=list)
    max_prompt_chars: int = 12000
    #: ``both`` (default), ``overview`` (only ``_Architecture.md``) or ``per-file``.
    mode: str = "both"
    #: Language of the notes in the vault (local flow), e.g. "German".
    language: str = "English"
    adr: AdrConfig = field(default_factory=AdrConfig)

    @property
    def per_file_notes(self) -> bool:
        """Write one note per changed file (stage 1)."""
        return self.mode in ("both", "per-file")

    @property
    def architecture_note(self) -> bool:
        """Maintain the overview note ``_Architecture.md`` (stage 3)."""
        return self.mode in ("both", "overview")


def _mode(raw: dict) -> str:
    """``mode`` from the config; the legacy ``architecture_note: false`` means ``per-file``."""
    mode = raw.get("mode")
    if mode is None:
        return "both" if raw.get("architecture_note", True) else "per-file"
    mode = str(mode).strip().lower()
    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r} in the config ({', '.join(MODES)})")
    return mode


def _resolve_env_placeholders(value: str | None) -> str | None:
    """Replace ``${ENV_VAR}`` in a string with the environment variable's value."""
    if not value:
        return value
    return _ENV_VAR_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)


def _verify_setting(value, project_root: Path) -> bool | str:
    """``true``/``false`` stay booleans; anything else is a certificate path."""
    if isinstance(value, bool):
        return value
    path = Path(str(value)).expanduser()
    return str(path if path.is_absolute() else project_root / path)


def load_config(project_root: Path) -> DocdriftConfig:
    """Load and validate ``.docdrift/config.yaml`` from the target project."""
    config_path = project_root / CONFIG_RELATIVE_PATH
    if not config_path.exists():
        raise FileNotFoundError(
            f"No configuration found at {config_path}. Run `docdrift init` first."
        )

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}

    obsidian_raw = raw.get("obsidian", {}) or {}
    vault_path_raw = obsidian_raw.get("vault_path")
    obsidian = ObsidianConfig(
        access_mode=obsidian_raw.get("access_mode", "local"),
        vault_path=(
            Path(_resolve_env_placeholders(vault_path_raw)).expanduser()
            if vault_path_raw
            else None
        ),
        rest_api_base_url=_resolve_env_placeholders(obsidian_raw.get("rest_api_base_url")),
        rest_api_key=_resolve_env_placeholders(obsidian_raw.get("rest_api_key")),
        rest_api_verify=_verify_setting(obsidian_raw.get("rest_api_verify", True), project_root),
    )

    github_raw = raw.get("github_docs", {}) or {}
    github_docs = GithubDocsConfig(
        enabled=github_raw.get("enabled", True),
        precheck=github_raw.get("precheck", True),
        assignee=github_raw.get("assignee", "copilot"),
        docs_paths=github_raw.get("docs_paths", ["docs/", "README.md"]),
    )

    adr_raw = raw.get("adr", {}) or {}
    adr = AdrConfig(
        enabled=adr_raw.get("enabled", True),
        threshold=int(adr_raw.get("threshold", 4)),
    )
    # `adr.location` (docdrift 0.2 pre-release) is obsolete and silently ignored:
    # ADRs are technical docs and always go to the vault.

    playbook_dir_raw = raw.get("playbook_dir")
    return DocdriftConfig(
        repo_name=raw.get("repo_name", project_root.name),
        api_base_url=raw.get("api_base_url", "http://localhost:11434/v1"),
        model=raw.get("model", "qwen2.5-coder:14b"),
        obsidian=obsidian,
        github_docs=github_docs,
        playbook_dir=(project_root / playbook_dir_raw) if playbook_dir_raw else None,
        ignore=raw.get("ignore", []),
        max_prompt_chars=int(raw.get("max_prompt_chars", 12000)),
        mode=_mode(raw),
        language=str(raw.get("language") or "English"),
        adr=adr,
    )
