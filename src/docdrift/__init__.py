"""docdrift - keep project documentation in sync with the code.

Public Python API (the CLI in :mod:`docdrift.cli` is a thin wrapper around it)::

    from docdrift import run_documentation

    result = run_documentation("path/to/repo")   # uses .docdrift/config.yaml
    print(result.documented, result.architecture_updated, result.adr_drafts)

The merge flow (GitHub Actions, Copilot) is in :mod:`docdrift.merge.task`
(``build_copilot_task``).
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from docdrift.local.pipeline import RunResult, draft_adr, run_documentation
from docdrift.merge.task import PullRequest, build_copilot_task

try:
    __version__ = _version("docdrift")  # single source: pyproject.toml
except PackageNotFoundError:  # pragma: no cover - running from a plain checkout
    __version__ = "0.0.0"

__all__ = [
    "PullRequest",
    "RunResult",
    "__version__",
    "build_copilot_task",
    "draft_adr",
    "run_documentation",
]
