"""Merge flow: product documentation in the repository, written by the Copilot coding agent.

Runs in GitHub Actions after a merge into ``main``; checks whether users could
notice the change and, if so, opens an issue for Copilot. No LLM on the runner,
never technical docs. Entry point: :func:`docdrift.merge.task.build_copilot_task`.
"""
