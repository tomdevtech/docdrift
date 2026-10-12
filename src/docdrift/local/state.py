"""Machine-local run state, stored as ``.docdrift/state/<repo>.json``.

The state is not version-controlled (see ``.gitignore``) because it differs per
machine. It remembers

* ``last_sha``     - the last commit that was fully documented,
* ``fingerprint``  - structural fingerprint of the project when the
  architecture note was last generated (see :meth:`ProjectModel.fingerprint`),
* ``adrs``         - ADR drafts created so far (used for numbering and to avoid
  drafting the same ADR twice).

:func:`run_lock` keeps two runs from working on the same repository at once.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class State:
    """Persistent state of one repository."""

    last_sha: str | None = None
    fingerprint: str | None = None
    adrs: list[dict] = field(default_factory=list)


def _state_file(state_dir: Path, repo_name: str) -> Path:
    return state_dir / f"{repo_name}.json"


def load_state(state_dir: Path, repo_name: str) -> State:
    """Load the state; return an empty one on the first run.

    Falls back to the legacy ``<repo>-last-sha`` file of docdrift 0.1.
    """
    path = _state_file(state_dir, repo_name)
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return State(
            last_sha=raw.get("last_sha"),
            fingerprint=raw.get("fingerprint"),
            adrs=raw.get("adrs", []),
        )
    legacy = state_dir / f"{repo_name}-last-sha"
    if legacy.exists():
        return State(last_sha=legacy.read_text(encoding="utf-8").strip() or None)
    return State()


def save_state(state_dir: Path, repo_name: str, state: State) -> None:
    """Write the state to disk (creating the directory if needed)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    _state_file(state_dir, repo_name).write_text(
        json.dumps(asdict(state), indent=2), encoding="utf-8"
    )


# --- run lock -------------------------------------------------------------------

#: A lock older than this is considered left over from a crashed run.
LOCK_STALE_SECONDS = 3 * 60 * 60


class RunLocked(RuntimeError):
    """Another docdrift run is active for this repository."""


@contextmanager
def run_lock(state_dir: Path):
    """Prevent two runs at once (e.g. a background hook run and a manual run).

    The lock is a file created atomically (``O_EXCL``). A lock older than
    :data:`LOCK_STALE_SECONDS` is taken over - process checks are not reliable
    across platforms, the age is.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "run.lock"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - path.stat().st_mtime
            except FileNotFoundError:
                continue  # released in the meantime
            if age < LOCK_STALE_SECONDS:
                raise RunLocked(
                    f"Another docdrift run is active (lock {path}, {int(age)}s old). "
                    "Delete the file if no run is active."
                ) from None
            path.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"pid {os.getpid()} since {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            yield
        finally:
            path.unlink(missing_ok=True)
        return
    raise RunLocked(f"Could not acquire {path}.")
