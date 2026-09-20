"""Command-line interface for docdrift.

Entry point (see pyproject.toml -> [project.scripts]):
    docdrift = "docdrift.cli:main"

Commands:
    docdrift init                  -> local trigger: pre-push hook + config.yaml
    docdrift init-github-obsidian  -> GitHub trigger for Flow 1 (Obsidian docs)
    docdrift init-github-product   -> GitHub trigger for Flow 2 (Copilot product docs)
    docdrift run                   -> manual Flow 1 run without pushing

The LLM user prompt is built directly as an f-string here instead of using
prompts/templates/update_doc.jinja. This keeps the initial working version
simple. The Jinja template remains available as a reference and extension
point if the prompt logic becomes more complex.
"""

import fnmatch
import importlib.resources
import stat
import subprocess
from pathlib import Path

import click
import frontmatter

from docdrift import config as config_module
from docdrift import git_utils, state
from docdrift.llm_client import ChatMessage, LLMClient
from docdrift.obsidian.base import resolve_note_path
from docdrift.obsidian.factory import get_writer
from docdrift.playbook_loader import load_playbook, resolve_playbook_name


def _find_repo_root() -> Path:
    try:
        output = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise click.ClickException(
            "No Git repository found. Run this command from the repository root."
        ) from exc
    return Path(output)


@click.group()
@click.version_option()
def main() -> None:
    """docdrift CLI."""


@main.command()
def init() -> None:
    """Initialize the local Git hook trigger for Flow 1."""
    repo_root = _find_repo_root()

    config_path = config_module.write_default_config(repo_root)
    click.echo(f"Configuration created: {config_path}")

    hook_template = (
        importlib.resources.files("docdrift")
        .joinpath("hooks", "pre-push.template")
        .read_text(encoding="utf-8")
    )
    hook_path = repo_root / ".git" / "hooks" / "pre-push"
    hook_path.write_text(hook_template, encoding="utf-8")
    hook_path.chmod(hook_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    click.echo(f"Git hook installed: {hook_path}")

    state_dir = repo_root / ".docdrift" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    _ensure_gitignore_entry(repo_root, ".docdrift/state/")

    click.echo("Done. Review .docdrift/config.yaml before pushing.")


def _ensure_gitignore_entry(repo_root: Path, entry: str) -> None:
    """Add `entry` to the target repository's .gitignore if it is not present.

    This prevents local state files that record the most recently documented
    commit from being committed accidentally. This information is local and
    may differ between machines and users.
    """
    gitignore_path = repo_root / ".gitignore"
    existing = gitignore_path.read_text(encoding="utf-8") if gitignore_path.exists() else ""
    if entry in existing.splitlines():
        return
    separator = "\n" if existing and not existing.endswith("\n") else ""
    with gitignore_path.open("a", encoding="utf-8") as f:
        f.write(f"{separator}{entry}\n")


def _install_workflow(repo_root: Path, template_filename: str, target_filename: str) -> None:
    workflow_text = (
        importlib.resources.files("docdrift")
        .joinpath("github_workflows", template_filename)
        .read_text(encoding="utf-8")
    )
    target_dir = repo_root / ".github" / "workflows"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / target_filename
    target_path.write_text(workflow_text, encoding="utf-8")
    click.echo(f"Workflow installed: {target_path}")


@main.command("init-github-obsidian")
def init_github_obsidian() -> None:
    """Install the GitHub Actions workflow for Flow 1 (Obsidian docs)."""
    repo_root = _find_repo_root()
    _install_workflow(repo_root, "obsidian-docs.yml.template", "obsidian-docs.yml")
    click.echo(
        "Note: review the self-hosted vs. GitHub-hosted runner setting in the "
        "workflow file (see docs/self-hosted-runner-setup.md or "
        "docs/obsidian-rest-api-setup.md)."
    )


@main.command("init-github-product")
def init_github_product() -> None:
    """Install the GitHub Actions workflow for Flow 2 (Copilot product docs)."""
    repo_root = _find_repo_root()
    _install_workflow(
        repo_root, "product-docs-on-merge.yml.template", "product-docs-on-merge.yml"
    )


def _should_ignore(path: Path, patterns: list[str]) -> bool:
    path_str = path.as_posix()
    return any(
        fnmatch.fnmatch(path_str, pattern) or path_str.startswith(pattern.rstrip("/") + "/")
        for pattern in patterns
    )


def _build_user_prompt(
    repo_name: str, file_path: Path, source_code: str, diff: str, existing_doc: str
) -> str:
    existing_section = existing_doc or "(None available — new file or first documentation run)"
    return (
        f"# Context\n\nProject: {repo_name}\nFile: {file_path}\n\n"
        f"## Current source code\n\n```\n{source_code}\n```\n\n"
        f"## Diff since the last documentation run\n\n```diff\n{diff}\n```\n\n"
        f"## Existing documentation\n\n{existing_section}\n\n"
        "Update the documentation according to the playbook."
    )


@main.command()
@click.option("--from", "from_sha", default=None, help="Starting commit (SHA)")
@click.option("--to", "to_sha", default=None, help="Ending commit (SHA), default: HEAD")
def run(from_sha: str | None, to_sha: str | None) -> None:
    """Run a Flow 1 documentation update."""
    repo_root = _find_repo_root()
    cfg = config_module.load_config(repo_root)

    to_sha = to_sha or git_utils.get_current_head(repo_root)

    state_dir = repo_root / ".docdrift" / "state"
    last_sha = state.get_last_documented_sha(state_dir, cfg.repo_name)
    from_sha = from_sha or last_sha or git_utils.get_first_commit(repo_root)

    if from_sha == to_sha:
        click.echo("Nothing new to document.")
        return

    changed_files = git_utils.get_changed_files(repo_root, from_sha, to_sha)
    changed_files = [f for f in changed_files if not _should_ignore(f, cfg.ignore)]
    # Skip deleted files because they no longer exist in the working tree.
    changed_files = [f for f in changed_files if (repo_root / f).exists()]

    if not changed_files:
        click.echo("No relevant changes found.")
        state.set_last_documented_sha(state_dir, cfg.repo_name, to_sha)
        return

    writer = get_writer(cfg)
    client = LLMClient(cfg.api_base_url, cfg.model)

    for file_path in changed_files:
        click.echo(f"Documenting {file_path} ...")

        diff = git_utils.get_file_diff(repo_root, file_path, from_sha, to_sha)
        source_code = git_utils.get_file_content(repo_root, file_path, to_sha)

        playbook_name = resolve_playbook_name(file_path)
        playbook_text = load_playbook(playbook_name, cfg.playbook_dir)

        note_path = resolve_note_path(cfg.repo_name, file_path)
        _, existing_body = writer.read_note(note_path)

        user_prompt = _build_user_prompt(cfg.repo_name, file_path, source_code, diff, existing_body)

        response_text = client.chat(
            [
                ChatMessage(role="system", content=playbook_text),
                ChatMessage(role="user", content=user_prompt),
            ]
        )

        # The playbook instructs the model to return the complete Markdown
        # content (front matter and body), which is exactly the format expected
        # by frontmatter.loads().
        post = frontmatter.loads(response_text)
        writer.write_note(note_path, dict(post.metadata), post.content)

    state.set_last_documented_sha(state_dir, cfg.repo_name, to_sha)
    click.echo(f"Done. Documented {len(changed_files)} file(s).")


if __name__ == "__main__":
    main()
