"""Select the appropriate ObsidianWriter implementation at runtime.

This is the only place that branches on "if access_mode == ...". The rest of
docdrift (cli.py and git_utils.py) knows only the protocol from base.py.
"""

from docdrift.config import DocdriftConfig
from docdrift.obsidian.base import ObsidianWriter
from docdrift.obsidian.local_writer import LocalObsidianWriter
from docdrift.obsidian.rest_api_writer import RestApiObsidianWriter


def get_writer(config: DocdriftConfig) -> ObsidianWriter:
    """Return LocalObsidianWriter or RestApiObsidianWriter based on the config."""
    mode = config.obsidian.access_mode

    if mode == "local":
        if not config.obsidian.vault_path:
            raise ValueError("obsidian.vault_path is missing from the config (access_mode: local).")
        return LocalObsidianWriter(config.obsidian.vault_path)

    if mode == "rest_api":
        if not config.obsidian.rest_api_base_url or not config.obsidian.rest_api_key:
            raise ValueError(
                "obsidian.rest_api_base_url / rest_api_key are missing from the config "
                "(access_mode: rest_api)."
            )
        return RestApiObsidianWriter(
            config.obsidian.rest_api_base_url, config.obsidian.rest_api_key
        )

    raise ValueError(
        f"Unknown obsidian.access_mode: {mode!r} (expected 'local' or 'rest_api')"
    )
