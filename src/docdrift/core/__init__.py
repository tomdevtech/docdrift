"""Shared stage 0 of both flows: git access and the deterministic AST analysis.

No LLM, no vault, no GitHub - pure facts about what changed. ``local`` and
``merge`` build on this package and never on each other.
"""
