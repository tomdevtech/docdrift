"""Interchangeable Obsidian writers for local file access or the REST API.

config.obsidian.access_mode ("local" | "rest_api") selects the writer; see
factory.py. The rest of the code (cli.py, git_utils.py) uses only the protocol
from base.py and never either implementation directly.
"""

from docdrift.obsidian.factory import get_writer

__all__ = ["get_writer"]
