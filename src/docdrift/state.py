"""Track which commit was documented most recently.

State is stored locally per repository in
.docdrift/state/<repo_name>-last-sha and is not version-controlled (see
.gitignore).
"""

from pathlib import Path


def _state_file(state_dir: Path, repo_name: str) -> Path:
    return state_dir / f"{repo_name}-last-sha"


def get_last_documented_sha(state_dir: Path, repo_name: str) -> str | None:
    """Return the most recently documented commit SHA, if available."""
    path = _state_file(state_dir, repo_name)
    if not path.exists():
        return None
    content = path.read_text(encoding="utf-8").strip()
    return content or None


def set_last_documented_sha(state_dir: Path, repo_name: str, sha: str) -> None:
    """Store the new most recently documented commit SHA."""
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_file(state_dir, repo_name).write_text(sha, encoding="utf-8")
