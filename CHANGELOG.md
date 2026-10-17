# Changelog

All notable changes to docdrift are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
docdrift follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
release workflow uses the section of a version as the notes of its GitHub release.

## [Unreleased]

## [1.0.0] - 2026-10-17

First public release.

### Added

#### Local flow - technical docs in Obsidian

- Pre-push hook (`docdrift init`) that documents every push in the background with a
  local OpenAI-compatible LLM (LM Studio, Ollama); `DOCDRIFT_SYNC=1` runs it in the
  foreground, `DOCDRIFT_SKIP=1` skips it.
- Deterministic stage 0: Python AST analysis of every change (added, removed, renamed
  and moved functions, changed signatures and implementations) as ground truth for
  the model; formatting-only changes are skipped without an LLM call.
- `mode` setting and `docdrift run --mode`: `both` (one note per file plus the
  architecture note), `overview` (only the architecture note) or `per-file`.
- High-level architecture note `_Architecture.md`, built in two steps (one LLM call
  per package with its source code, then the project overview): overview,
  architecture, processes, features, scope, one section per package, plus module
  lists and a Mermaid module map computed from the AST.
- ADR drafts in the vault for significant changes (`status: proposed`), and
  `docdrift adr --from <sha>` for a manual draft.
- Notes keep a manual section below `<!-- AUTO-DOC-END -->`, record the commit and a
  content hash (unchanged files are never sent twice), and notes of deleted files are
  marked instead of removed.
- Vault access via the file system or Obsidian's Local REST API; a `vault_path` that
  already ends in `Projects` is used as its parent.
- `docdrift check` to verify config, vault, LLM server, model, context size and hook;
  `docdrift status` to show mode, model, vault target and the last documented commit.
- `language` setting for the language of the notes, and `playbook_dir` to override
  the bundled prompts.

#### Merge flow - product docs in the repository

- GitHub Actions workflow (`docdrift init-github-product`) that checks after every
  merged pull request whether users could notice the change and, if so, has
  `README.md` and `docs_paths` checked and updated in a pull request.
- `github_docs.engine`: `copilot` (an issue for the Copilot coding agent; posted
  unassigned if Copilot cannot be assigned) or `github-models` (free GitHub Models
  inference with find/replace edits per doc file; the workflow opens the docs pull
  request or comments that no update is needed).
- Strict separation: the merge flow writes product docs only, never architecture,
  internals or ADRs; docs pull requests and docs-only merges never start a new task.
- In the docdrift repository itself the workflow installs its own checkout, so
  `DOCDRIFT_PIP_SPEC` is only needed in other repositories.

#### Project

- Test pipeline on every pull request (Linux with Python 3.10 to 3.13, Windows).
- Release pipeline: every merge into `main` with a new version builds sdist and
  wheel, checks the wheel and creates the tag and GitHub release.
- Guides: [setup](docs/setup.md), [usage](docs/usage.md),
  [how it works](docs/how-it-works.md), [merge flow setup](docs/copilot-merge-setup.md)
  and [measured runtimes](docs/runtime.md).

[Unreleased]: https://github.com/tomdevtech/docdrift/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/tomdevtech/docdrift/releases/tag/v1.0.0
