---
type: adr
adr: 3
status: proposed
date: 2026-10-12
---

# ADR 0003: ADR drafts are proposed-only and score-triggered

## Context

ADRs record *why* a decision was made, but code and diffs only show *what* changed.
A small local model cannot know the rationale, and drafting an ADR for every push
would flood the vault.

## Decision

A deterministic significance score (new modules, dependency changes, public API
changes in several files, moves, breaking commit messages) decides whether to call
the LLM at all (`adr.threshold`). Drafts are written with `status: proposed`, mark
unknown rationale as "please confirm", and list the signals that triggered them. Only a
human sets `accepted`. ADRs live only in the vault (see ADR 0004).

## Consequences

- Few, relevant drafts; the trigger is explainable and tunable.
- Every ADR needs a short human review.
- Rationale quality depends on commit messages.
