---
type: adr
adr: 4
status: accepted
date: 2026-10-12
---

# ADR 0004: Strict separation of technical and product documentation

## Context

A pre-release let ADRs go either to the vault or to the repository
(`adr.location`), and a later draft had the merge flow ask Copilot for "official"
ADRs in `docs/adr/`. Both blurred the line drawn in ADR 0001: technical knowledge
would have ended up in two places and been written by two different models. The
maintainer decided on a strict split.

## Decision

The local LLM writes all technical documentation - notes per file,
`_Architecture.md` and ADRs - and only into the Obsidian vault, on every push.
The Copilot coding agent writes only product documentation (`README.md`,
`docs_paths`) after a merge into `main`, and only if a deterministic pre-check
(`docs_check.py`) finds that users could notice the change. The Copilot issue
forbids architecture descriptions, internals, design rationale and ADRs, and
`docs/adr/` is never part of its scope. `adr.location` was removed.

## Consequences

- One owner per kind of documentation; technical notes never leak into the repo.
- ADRs are completed and accepted in the vault, not reviewed in a pull request.
- Merges that only change internals cost no Copilot request.
- If `docs/` mixes user and technical pages, `docs_paths` has to be narrowed.
