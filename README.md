# docdrift

Keeps your project documentation in sync with your code - automatically, with a
**local** LLM (Ollama / LM Studio) and no data leaving your machine.

> Status: under development.

## Two flows

| | Local flow - technical docs | Merge flow - product docs |
|---|---|---|
| **Content** | how it works: notes per file, architecture, ADRs | how to use it: README, user guides |
| **Trigger** | every `git push` (pre-push hook) | every pull request merged into `main` |
| **For** | you / your team (private) | users of the project (public) |
| **Where** | Obsidian vault | the repository (`README.md`, `docs/`) |
| **Written by** | a local LLM | the GitHub Copilot coding agent |
| **Only when needed** | formatting-only changes are skipped | a pre-check skips changes users cannot notice |
| **Review** | you read the notes | Copilot's pull request |

The separation is strict: the local LLM writes **all** technical documentation, and only
into the Obsidian vault. Copilot writes **only** product documentation and is told
explicitly not to write architecture descriptions, internals or ADRs. The flows are
independent; use either or both.

## What the local flow produces

For every push, docdrift writes into `<vault>/Projects/<repo>/`:

- **`_Architecture.md`** - the high-level architecture: overview, components,
  data flow and a module map (Mermaid). Describes the *project*, not single files.
  Regenerated only when the public structure changes.
- **one note per changed file** - purpose, public interface, how it works, changelog.
  Each note records the commit and a hash of the content it describes, so unchanged
  files are never sent to the model twice. Notes of deleted files are kept and marked.
- **`ADR/NNNN-*.md`** - Architecture Decision Records, only for significant changes
  (new modules, new dependencies, API changes across files, breaking commits). They
  start as `status: proposed` and mark the unknown *why* with "please confirm"; you
  complete them and set `accepted`.

Everything below `<!-- AUTO-DOC-END -->` in a note is yours and survives regeneration.

## What the merge flow does

After a merge into `main`, a GitHub Action runs `docdrift copilot-task` (no LLM on the
runner):

1. **Is it relevant for users?** A deterministic pre-check looks for public API changes,
   new or removed modules, packaging changes, files it cannot analyse, `feat`/breaking
   commits, and places where `README.md`/`docs/` mention changed code. A bug fix inside
   a function nobody documents stops here - no Copilot request.
2. **Copilot checks and updates.** Otherwise the Copilot coding agent gets an issue with
   these hints and the exact `file:line` mentions. It first decides whether the docs are
   still correct, updates only what is outdated, or changes nothing and says so in its
   pull request (which you then close).

Copilot only touches `README.md` and `docs_paths`; technical documentation is out of
its scope.
Copilot's own PRs and docs-only merges are skipped, so the flow cannot loop.
Setup: [docs/copilot-merge-setup.md](docs/copilot-merge-setup.md).

## How it works

```
git diff  ->  [0] AST analysis  ->  [1] per-file notes (LLM)
 (what)       deterministic         [2] ADR draft, if significant (LLM)
                                    [3] _Architecture.md, if structure changed (LLM)
```

The LLM never has to work out *what* changed from a raw diff. Stage 0 uses
Python's `ast` module to compute added/removed/renamed/moved functions, changed
signatures and changed implementations, plus the import graph of the project.
The model receives these facts (and falls back to the plain diff for non-Python
files). Details: [docs/how-it-works.md](docs/how-it-works.md). The reasoning behind
the design is recorded in [docs/adr/](docs/adr/).

## Installation

```bash
pip install -e ".[dev]"      # from a local clone; requires Python >= 3.10
```

> **Not from PyPI:** the name `docdrift` on PyPI belongs to a different project.
> Install from your clone or from Git (`pip install "git+https://github.com/<you>/docdrift.git@<tag>"`).

You need a running local LLM server with a model loaded, e.g.
`ollama pull qwen2.5-coder:14b`, and an Obsidian vault.

## Quick start

```bash
cd your-project
docdrift init                  # creates .docdrift/config.yaml, installs the pre-push hook
# edit .docdrift/config.yaml (vault_path, model, ...)
docdrift check                 # verifies config, vault, LLM + model, hook
docdrift run                   # first run in the foreground; afterwards just git push
```

Other triggers and flows:

```bash
docdrift init-github-product   # merge flow: Copilot keeps README/docs up to date
docdrift init-github-obsidian  # local flow via GitHub Actions instead of the hook
docdrift adr --from <sha>      # ADR draft in the vault for a commit range, ignoring the threshold
```

The hook never blocks or slows down a push: docdrift runs in the background and logs
to `.docdrift/logs/docdrift.log`. `DOCDRIFT_SYNC=1 git push` runs it in the foreground,
`DOCDRIFT_SKIP=1 git push` skips it. Failed files are retried on the next run; a lock
prevents two runs at once. `docdrift run --force` regenerates all notes of a range.

## Use from Python

```python
from docdrift import run_documentation, draft_adr

result = run_documentation("path/to/repo")     # reads .docdrift/config.yaml
print(result.documented, result.architecture_updated, result.adr_drafts)
```

The merge flow is available as `docdrift.build_copilot_task(repo, cfg, PullRequest(...))`
(module `docdrift.merge.task`).

`run_documentation` accepts an explicit `cfg`, `client` (any object with
`chat(messages) -> str`) and `vault`, so it can be embedded or tested without a server.

## Configuration

All keys are optional except `obsidian`. Full example: [examples/docdrift.config.example.yaml](examples/docdrift.config.example.yaml).

| Key | Default | Meaning |
|---|---|---|
| `api_base_url`, `model` | Ollama, `qwen2.5-coder:14b` | local OpenAI-compatible server |
| `max_prompt_chars` | `12000` | prompt budget; large files are reduced to a signature outline |
| `language` | `English` | language of the vault notes, e.g. `German` (headings stay English) |
| `obsidian.access_mode` | `local` | `local` (files) or `rest_api` (plugin + tunnel) |
| `obsidian.vault_path` | - | vault **root** (notes go to `Projects/<repo>/`); must exist |
| `obsidian.rest_api_verify` | `true` | TLS check for `rest_api`: `false` or the plugin's certificate path |
| `architecture_note` | `true` | maintain `_Architecture.md` |
| `adr.enabled` / `adr.threshold` | `true` / `4` | ADRs in the vault (local flow only) |
| `github_docs.enabled` / `precheck` | `true` / `true` | merge flow: product docs task; skip changes users cannot notice |
| `github_docs.assignee`, `docs_paths` | `copilot`, `docs/` + `README.md` | who gets the issue; which files are product docs |
| `ignore` | `[]` | globs or directories never documented (`.docdrift/` always is) |
| `playbook_dir` | - | override bundled prompts (`base`, `python`, `typescript`, `fallback`, `architecture`, `adr`) |

## Project layout

```
src/docdrift/
  __init__.py                    public Python API (the only top-level file)
  core/                          shared - no LLM, no vault, no GitHub
    config.py                    .docdrift/config.yaml
    git_utils.py                 git CLI wrapper
    analysis.py                  AST symbols, change detection, project model
    changes.py                   commit range -> analysed changes, ignore rules
  local/                         local flow: technical docs -> Obsidian (local LLM)
    pipeline.py                  the four stages; library entry point
    adr.py                       significance score, ADR drafting
    vault.py  llm_client.py  state.py  playbook_loader.py
    playbooks/  hooks/  github_workflows/
  merge/                         merge flow: product docs -> Copilot (no LLM)
    task.py                      the Copilot issue for a merged PR
    docs_check.py                pre-check: could the product docs be outdated?
    github_workflows/
  setup/                         installation and diagnosis
    install.py                   config template, pre-push hook, workflows, .gitignore
    doctor.py                    `docdrift check`
  cli/                           `docdrift <command>` - thin layer, no logic
    commands.py  __main__.py
docs/                            usage, how-it-works, setup guides, adr/
```

Every module belongs to a feature. `core` is shared; `local` and `merge` never
import each other, and only `cli` uses everything (enforced by
`tests/test_architecture.py`).

## Development

```bash
pytest          # 132 tests, no LLM server, Obsidian or GitHub needed
ruff check src tests
```

## License

MIT
