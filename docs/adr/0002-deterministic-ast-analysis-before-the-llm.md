---
type: adr
adr: 2
status: proposed
date: 2026-10-12
---

# ADR 0002: Deterministic AST analysis before the LLM

## Context

Raw `git diff` input gets noisy for multi-file refactorings, and small local models
(7b-14b) lose track of what actually changed. The first AST version also had gaps:
implementation changes, methods and non-Python files were reported as "no changes".
(Rationale for choosing the AST approach over diffs alone: external review feedback -
please confirm.)

## Decision

All facts about *what* changed are computed deterministically in `analysis.py` with the
stdlib `ast` module: symbol changes including methods, body hashes, rename/move
detection across files, an import graph and a structural fingerprint. The LLM only
writes prose from these facts and falls back to the plain diff where no analysis is
possible (non-Python files).

## Consequences

- Cleaner, smaller prompts; formatting-only commits cost no LLM call.
- The architecture module map and the ADR trigger are reliable and testable without a model.
- Structural analysis is Python-only; other languages need another parser (e.g. tree-sitter).
