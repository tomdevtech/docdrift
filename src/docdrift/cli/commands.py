"""Command-line interface (entry point ``docdrift``, see ``pyproject.toml``).

Commands:
    docdrift init                   local trigger: pre-push hook + config
    docdrift init-github-obsidian   local flow via GitHub Actions instead of the hook
    docdrift init-github-product    merge flow: Copilot keeps README/docs up to date
    docdrift check                  verify config, vault, LLM and hook
    docdrift status                 show mode, model, vault target and the last documented commit
    docdrift run                    document everything since the last run
    docdrift adr                    draft an ADR for a commit range
    docdrift copilot-task           (CI) build the docs task (Copilot issue) for a merged pull request
    docdrift llm-docs               (CI) update the docs of a merged pull request with an LLM

All logic lives in the feature packages - :mod:`docdrift.local`,
:mod:`docdrift.merge` and :mod:`docdrift.setup`; this module only parses
arguments and prints progress.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import click

from docdrift.core import config as config_module
from docdrift.core import git_utils
from docdrift.local import pipeline
from docdrift.local.state import RunLocked, load_state
from docdrift.local.vault import architecture_note_path, vault_root
from docdrift.merge import llm as merge_llm
from docdrift.merge import task as merge
from docdrift.setup import doctor, install


def _find_repo_root() -> Path:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise click.ClickException(
            "No Git repository found. Run this command from the repository root."
        ) from exc
    return Path(output)


@click.group()
@click.version_option()
def main() -> None:
    """docdrift - keep documentation in sync with your code."""


@main.command()
def init() -> None:
    """Create the config and install the local pre-push hook (local flow)."""
    config, hook, backup = install.install_local(_find_repo_root())
    click.echo(f"Configuration: {config}")
    if backup:
        click.echo(f"Existing pre-push hook saved as {backup.name}")
    click.echo(f"Git hook installed: {hook}")
    click.echo(
        "Done. Review .docdrift/config.yaml, then run `docdrift check` and a first "
        "`docdrift run` in the foreground."
    )


@main.command("init-github-obsidian")
def init_github_obsidian() -> None:
    """Install the GitHub Actions workflow for the local flow (Obsidian docs)."""
    target = install.install_workflow(_find_repo_root(), "local", "obsidian-docs")
    click.echo(f"Workflow installed: {target}")
    click.echo(
        "Note: review the runner setting in the workflow "
        "(docs/self-hosted-runner-setup.md, docs/obsidian-rest-api-setup.md)."
    )


@main.command("init-github-product")
def init_github_product() -> None:
    """Install the merge workflow: Copilot keeps README/docs up to date after each merge."""
    repo_root = _find_repo_root()
    target = install.install_workflow(repo_root, "merge", "product-docs-on-merge")
    click.echo(f"Workflow installed: {target}")
    click.echo(
        "Next steps - choose github_docs.engine in .docdrift/config.yaml:\n"
        "  copilot (needs a Copilot Pro/Pro+/Business/Enterprise licence):\n"
        "    1. Enable the Copilot coding agent for this repository.\n"
        "    2. Add a repository secret DOCDRIFT_COPILOT_TOKEN: a personal access token\n"
        "       (fine-grained: Actions, Contents, Issues, Pull requests = read/write). GitHub only lets\n"
        "       user tokens assign Copilot; without it the issue is created unassigned.\n"
        "  llm (any OpenAI-compatible LLM, e.g. OpenRouter's free models):\n"
        "    1. Add a repository secret DOCDRIFT_LLM_API_KEY (e.g. an OpenRouter key).\n"
        "    2. Settings > Actions > General: allow GitHub Actions to create pull requests.\n"
        "  Then commit .docdrift/config.yaml - the workflow reads it."
    )
    try:
        cfg = config_module.load_config(repo_root)
    except FileNotFoundError:
        click.echo("Warning: no .docdrift/config.yaml yet - run `docdrift init` first.")
        return
    if not cfg.github_docs.enabled:
        click.echo(
            "Note: github_docs.enabled is false in .docdrift/config.yaml - set it to true, "
            "otherwise the workflow does nothing."
        )


@main.command()
@click.option("--no-probe", is_flag=True, help="Skip the test prompt to the model.")
def check(no_probe: bool) -> None:
    """Verify the setup: config, vault path, LLM server and model, pre-push hook."""
    marks = {"ok": "OK  ", "warn": "WARN", "error": "FAIL"}
    findings = doctor.run_checks(_find_repo_root(), probe=not no_probe)
    for f in findings:
        click.echo(f"[{marks[f.level]}] {f.topic:<6} {f.message}")
    if any(f.level == "error" for f in findings):
        sys.exit(1)


@main.command()
def status() -> None:
    """Show mode, model, vault target and the last documented commit (no LLM call)."""
    root = _find_repo_root()
    try:
        cfg = config_module.load_config(root)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    st = load_state(root / ".docdrift" / "state", cfg.repo_name)
    obs = cfg.obsidian
    if obs.access_mode == "local" and obs.vault_path:
        target = str(vault_root(obs.vault_path) / "Projects" / cfg.repo_name)
    else:
        target = f"{obs.rest_api_base_url or '(rest_api_base_url missing)'} (REST API)"
    head = git_utils.get_current_head(root)
    if not st.last_sha:
        last = "never - the next run documents the whole repository"
    elif st.last_sha == head:
        last = f"{st.last_sha[:8]} (up to date with HEAD)"
    else:
        last = f"{st.last_sha[:8]} (HEAD is {head[:8]} - run `docdrift run` to catch up)"
    click.echo(f"repo        {cfg.repo_name}")
    click.echo(f"mode        {cfg.mode}")
    click.echo(f"model       {cfg.model} at {cfg.api_base_url}")
    click.echo(f"notes       {target}")
    if cfg.architecture_note:
        click.echo(f"overview    {architecture_note_path(cfg.repo_name)}")
    click.echo(f"documented  {last}")
    click.echo(f"ADR drafts  {len(st.adrs)}")


@main.command()
@click.option("--from", "from_sha", default=None, help="Start commit (default: last documented).")
@click.option("--to", "to_sha", default=None, help="End commit (default: HEAD).")
@click.option("--force", is_flag=True, help="Regenerate notes and overview even if they are already current.")
@click.option(
    "--mode", type=click.Choice(config_module.MODES, case_sensitive=False), default=None,
    help="Override `mode` from the config for this run: per-file notes, the overview (HLA) or both.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Only show what would be documented: no LLM call, nothing written to the vault.",
)
def run(from_sha: str | None, to_sha: str | None, force: bool, mode: str | None, dry_run: bool) -> None:
    """Update the technical documentation (per-file notes, ADR drafts, architecture overview)."""
    args = {
        "from_sha": from_sha, "to_sha": to_sha, "log": click.echo, "force": force,
        "mode": mode.lower() if mode else None,
    }
    try:
        if dry_run:
            _print_plan(pipeline.plan_documentation(_find_repo_root(), **args))
            return
        result = pipeline.run_documentation(_find_repo_root(), **args)
    except (RunLocked, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if not result.ok:
        sys.exit(1)


def _print_plan(plan: pipeline.RunPlan) -> None:
    if plan.up_to_date:
        click.echo("Nothing new to document.")
    else:
        click.echo(f"Range {plan.from_sha[:8]}..{plan.to_sha[:8]} (mode: {plan.mode})")
    if plan.document:
        click.echo(f"Would document {len(plan.document)} file(s):")
        click.echo("".join(f"  {path}\n" for path in plan.document), nl=False)
    if plan.skip:
        click.echo(f"Would skip {len(plan.skip)} file(s):")
        click.echo("".join(f"  {path} ({why})\n" for path, why in plan.skip), nl=False)
    if plan.deleted:
        click.echo(f"Would mark {len(plan.deleted)} note(s) of deleted files:")
        click.echo("".join(f"  {path}\n" for path in plan.deleted), nl=False)
    click.echo(f"ADR draft: {plan.adr}")
    click.echo(f"Architecture overview: {plan.overview}")
    if not plan.vault_checked:
        click.echo("Note: the vault was not reachable, so current notes are listed as 'would document'.")
    click.echo("Dry run - nothing was sent to the LLM or written to the vault.")


@main.command("adr")
@click.option("--from", "from_sha", default=None, help="Start commit (default: last documented).")
@click.option("--to", "to_sha", default=None, help="End commit (default: HEAD).")
def adr(from_sha: str | None, to_sha: str | None) -> None:
    """Draft an ADR for a commit range, regardless of the significance threshold."""
    try:
        pipeline.draft_adr(_find_repo_root(), from_sha=from_sha, to_sha=to_sha, log=click.echo)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise click.ClickException(str(exc)) from exc


def _pr_options(func):
    """The merged pull request, as passed by the merge workflow."""
    options = [
        click.option("--pr", "pr_number", type=int, required=True, help="Number of the merged pull request."),
        click.option("--base", "base_sha", required=True, help="pull_request.base.sha"),
        click.option("--head", "head_sha", required=True, help="pull_request.head.sha"),
        click.option("--title", default="", help="Title of the pull request."),
        click.option("--head-ref", default="", help="Branch name of the pull request."),
        click.option(
            "--body-file", type=click.Path(dir_okay=False, path_type=Path), default=None,
            help="File with the pull request description.",
        ),
    ]
    for option in reversed(options):
        func = option(func)
    return func


def _merge_task(pr_number, base_sha, head_sha, title, head_ref, body_file):
    """Load the config and build the docs task of a merged pull request."""
    repo_root = _find_repo_root()
    try:
        cfg = config_module.load_config(repo_root)
    except FileNotFoundError as exc:
        raise click.ClickException(f"{exc} Is .docdrift/config.yaml committed?") from exc
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    body = body_file.read_text(encoding="utf-8") if body_file and body_file.exists() else ""
    pr = merge.PullRequest(
        number=pr_number, base_sha=base_sha, head_sha=head_sha,
        title=title, body=body, head_ref=head_ref,
    )
    try:
        result = merge.build_copilot_task(repo_root, cfg, pr, log=click.echo)
    except subprocess.CalledProcessError as exc:
        raise click.ClickException(
            f"git failed: {exc.stderr or exc}. The workflow needs fetch-depth: 0 and the PR head."
        ) from exc
    return repo_root, cfg, pr, result


def _github_output(**values: str) -> None:
    """Pass step outputs to later workflow steps (no-op outside GitHub Actions)."""
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.writelines(f"{key}={value}\n" for key, value in values.items())


@main.command("copilot-task")
@_pr_options
@click.option(
    "--output", type=click.Path(dir_okay=False, path_type=Path), required=True,
    help="Where to write the issue as JSON. Not written when there is nothing to do.",
)
def copilot_task(
    pr_number: int, base_sha: str, head_sha: str, title: str, head_ref: str,
    body_file: Path | None, output: Path,
) -> None:
    """(CI) Check a merged PR and build the docs task (Copilot issue); no LLM involved."""
    _, cfg, _, result = _merge_task(pr_number, base_sha, head_sha, title, head_ref, body_file)
    if output.exists():
        output.unlink()
    _github_output(engine=cfg.github_docs.engine, task=str(result.task is not None).lower())
    if result.task is None:
        click.echo(f"Nothing to do: {result.reason}.")
        return
    output.write_text(json.dumps(result.task.to_issue(), ensure_ascii=False, indent=2), encoding="utf-8")
    click.echo(f"Docs task written to {output}: {result.task.title}")


@main.command("llm-docs")
@_pr_options
@click.option(
    "--summary", type=click.Path(dir_okay=False, path_type=Path), required=True,
    help="Where to write the Markdown summary (pull request body or comment).",
)
def llm_docs(
    pr_number: int, base_sha: str, head_sha: str, title: str, head_ref: str,
    body_file: Path | None, summary: Path,
) -> None:
    """(CI) Update the product docs of a merged PR with an LLM (key: DOCDRIFT_LLM_API_KEY)."""
    repo_root, cfg, pr, result = _merge_task(pr_number, base_sha, head_sha, title, head_ref, body_file)
    if summary.exists():
        summary.unlink()
    if result.task is None:
        click.echo(f"Nothing to do: {result.reason}.")
        _github_output(changed="false", summary="false")
        return
    gd = cfg.github_docs
    key = os.environ.get("DOCDRIFT_LLM_API_KEY", "")
    if not key and not gd.llm_api_base_url.startswith(("http://localhost", "http://127.0.0.1")):
        raise click.ClickException(
            f"DOCDRIFT_LLM_API_KEY is not set - add it as a repository secret ({gd.llm_api_base_url} needs a key)."
        )
    client = merge_llm.LLMClient(gd.llm_api_base_url, gd.llm_model, api_key=key)
    try:
        outcome = merge_llm.update_docs(repo_root, cfg, result.task, pr, client=client, log=click.echo)
    except merge_llm.LLMError as exc:
        raise click.ClickException(str(exc)) from exc
    summary.write_text(outcome.summary(pr, gd.llm_model), encoding="utf-8")
    _github_output(changed=str(outcome.changed).lower(), summary="true")
    click.echo("Docs updated - a pull request follows." if outcome.changed else "No documentation update needed.")


if __name__ == "__main__":
    main()
