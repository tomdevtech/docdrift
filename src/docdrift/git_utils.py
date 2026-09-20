"""Git operations for changed files, diffs, and documented commits.

Uses subprocess and the `git` CLI instead of a library such as GitPython to
avoid adding a large dependency.
"""

import subprocess
from pathlib import Path


def _run_git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def get_current_head(repo_root: Path) -> str:
    """Return the current HEAD commit SHA."""
    return _run_git(repo_root, ["rev-parse", "HEAD"]).strip()


def get_first_commit(repo_root: Path) -> str:
    """Return the repository's first commit as a fallback for the initial run."""
    output = _run_git(repo_root, ["rev-list", "--max-parents=0", "HEAD"]).strip()
    return output.splitlines()[0]


def get_changed_files(repo_root: Path, from_sha: str, to_sha: str) -> list[Path]:
    """Return files changed between two commits."""
    output = _run_git(repo_root, ["diff", "--name-only", from_sha, to_sha])
    return [Path(line) for line in output.splitlines() if line.strip()]


def get_file_diff(repo_root: Path, path: Path, from_sha: str, to_sha: str) -> str:
    """Return the diff for one file between two commits."""
    return _run_git(repo_root, ["diff", from_sha, to_sha, "--", path.as_posix()])


def get_file_content(repo_root: Path, path: Path, sha: str = "HEAD") -> str:
    """Return the complete file contents at a specific commit.

    Return an empty string if the file does not exist at that commit, for
    example because it was deleted. This is an expected case, not an error.
    """
    try:
        return _run_git(repo_root, ["show", f"{sha}:{path.as_posix()}"])
    except subprocess.CalledProcessError:
        return ""
