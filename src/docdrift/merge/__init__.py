"""Merge flow: product documentation in the repository.

Runs in GitHub Actions after a merge into ``main``; checks whether users could
notice the change and, if so, has the docs updated by the configured engine:
an issue for the Copilot coding agent (:mod:`docdrift.merge.task`) or edits by
an OpenAI-compatible LLM (:mod:`docdrift.merge.llm`). Never technical docs. Entry point:
:func:`docdrift.merge.task.build_copilot_task`.
"""
