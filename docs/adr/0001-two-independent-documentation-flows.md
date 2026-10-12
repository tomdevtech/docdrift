---
type: adr
adr: 1
status: accepted
date: 2026-10-12
---

# ADR 0001: Two independent documentation flows

## Context

Technical notes (how a module works, half-formed ideas) and product documentation
(README, docs/) have different audiences. Treating them the same makes one of them
wrong: private notes leak into public files, or product docs skip review.

## Decision

Flow 1 writes technical notes to a private Obsidian vault with a local LLM. Flow 2 lets
the GitHub Copilot coding agent update repository docs after each merged PR, so the
change arrives as a reviewable pull request. The flows share no state and can be used
separately.

## Consequences

- Private notes never touch the repository; product docs get normal review.
- Two code paths and two triggers to maintain.
- Flow 2 depends on the Copilot coding agent being available.
