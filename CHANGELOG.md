# Changelog

All notable changes to docdrift are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
docdrift follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). The
release workflow uses the section of a version as the notes of its GitHub release.

## [Unreleased]

### Fixed

- Merge flow engine `llm`: a free model that answers with its reasoning instead of the
  requested JSON no longer stops the run. docdrift asks OpenRouter for JSON mode, limits
  the answer length, asks once more for the JSON only, and skips a doc file whose model
  still does not answer usably (listed in the pull request or comment). The run fails
  only if no doc file got a usable answer.

## [1.0.1] - 2026-10-17

Fixes the merge flow without a Copilot licence: the engine `github-models` of 1.0.0
could not work because GitHub Models was retired.

### Added

- Merge flow engine `llm`: updates the product docs with any OpenAI-compatible LLM -
  by default OpenRouter's free models (`openrouter/free`), or Groq, Google Gemini, or
  a local LM Studio / Ollama on a self-hosted runner. Configured with
  `github_docs.llm_api_base_url`, `llm_model`, `llm_max_files` and `llm_max_chars`;
  the API key is the repository secret `DOCDRIFT_LLM_API_KEY`. Command:
  `docdrift llm-docs`.
- The `llm` engine tolerates reasoning blocks, code fences and text around the JSON
  answer, which free models often add.

### Changed

- [Setup guide](docs/setup.md) completed: prerequisites (Git for Windows), pipx and
  Windows paths, loading an LM Studio / Ollama model with enough context and finding its
  id, what success looks like after each step, OpenRouter account, privacy setting and
  key, and the merge workflow's `main` branch. More troubleshooting entries in
  [usage.md](docs/usage.md#troubleshooting).

### Removed

- Engine `github-models` and the `models_*` settings: GitHub Models was retired on
  2026-07-30, so the engine could not work. A config that still sets
  `engine: github-models` now fails with a hint to use `engine: llm`.

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
  unassigned if Copilot cannot be assigned) or `github-models` (GitHub Models
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
  [how it works](docs/how-it-works.md) and [merge flow setup](docs/copilot-merge-setup.md).

[Unreleased]: https://github.com/tomdevtech/docdrift/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/tomdevtech/docdrift/compare/v1.0.0...v1.0.1
[1.0.0]: https://github.com/tomdevtech/docdrift/releases/tag/v1.0.0
