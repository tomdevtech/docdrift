# How docdrift works

docdrift has two flows. The **local flow** (this page, stages 0-3) runs on every
push with a local LLM and writes to the Obsidian vault. The **merge flow** (last
section) runs in GitHub Actions after a merge into `main` and hands the repository
docs (README, `docs/`) to the Copilot coding agent - but only when the change can
matter to users. The split is strict: technical docs (notes, architecture, ADRs) come
only from the local flow, product docs only from Copilot. Both share stage 0.

## Local flow

A run (`docdrift run`, or `run_documentation()` from Python) covers a commit range:
from the last fully documented commit to `HEAD`. On the very first run the range
starts at Git's empty tree, so *every* file of the repository is documented.

### Stage 0 - deterministic analysis (`core/`)

No LLM, no I/O; works on source strings.

**Per file** (`parse_module`): functions, methods (`Class.method`), classes, imports,
and three hashes - the whole AST (`module_hash`), the implementation of every
function without its docstring (`body_hash`), and the top-level code that is not a
def/class/import (`rest_hash`). Hashes come from `ast.dump`, so they ignore
formatting and comments.

**Changes** (`analyze_changes`) compares old and new sources and reports per file:

| Marker | Meaning |
|---|---|
| `+ added` / `- removed` | symbol appeared / disappeared |
| `~ signature` | parameters or return type changed (also shown: "implementation changed too") |
| `~ body` | implementation changed, signature identical (or class fields changed) |
| `~ renamed`, `~ moved in/out` | identical body under another name or in another file |
| `+ import` / `- import` | import added / removed |
| `~ other` | module-level code (constants, module docstring) changed |

Rename detection pairs a removed and an added function only if their bodies are
identical, at least 6 AST nodes large, and the pair is unambiguous (1:1).
Files whose AST hash is unchanged are **skipped without an LLM call** (formatting or
comments only). Non-Python files and files with syntax errors are marked
`unsupported`, which tells the model to rely on the diff - never "nothing changed".

**Project model** (`build_project_model`): all modules at the target commit with
their public API, the internal import graph (relative and absolute imports resolved,
third-party ignored), entry points (modules nobody imports) and a **fingerprint**
over modules, public signatures, module docstrings and internal imports.
Implementation-only and private changes do not alter the fingerprint.

### Stage 1 - per-file notes

For each changed file the prompt contains the structural analysis, the git diff, the
existing note (without your manual section) and the source. If it exceeds
`max_prompt_chars`, the source is replaced by a signature outline, then diff and
existing note are cut. The reply is written to `Projects/<repo>/<file>.md`; docdrift
sets the front matter itself (the model must not invent dates).

### Stage 2 - ADR drafts (`local/adr.py`)

Whether a change deserves an ADR is decided by points, not by the model:

| Signal | Points |
|---|---|
| Python module added or removed | 3 |
| dependency manifest changed (`pyproject.toml`, `requirements*.txt`, `package.json`, ...) | 3 |
| public API changed in >= 3 files | 2 |
| symbols moved between files | 2 |
| commit message: `BREAKING CHANGE` / `type!:` (3), architecture/redesign/rewrite/migrate (2), `feat`/`refactor` (1) - highest one counts | 1-3 |
| >= 10 files touched | 1 |

At `adr.threshold` (default 4) the model drafts an ADR from the signals, commit
messages and structural changes. Because the code shows *what* but not *why*, the
playbook tells the model to write "(rationale not recorded - please confirm)" instead
of guessing. Drafts get `status: proposed` and a list of the signals that triggered
them. At most one draft is created per target commit, and only after stage 1 succeeded.
`docdrift adr --from <sha>` drafts one on demand.

Numbering and the index (`ADR/Index.md`) are maintained without the LLM. The
numbers are taken from both the state file and the index note, so they stay unique
even when `.docdrift/state/` is lost (new machine, CI runner). Drafts never touch
the repository - ADRs are technical docs and stay in the vault.

### Stage 3 - architecture overview

`_Architecture.md` is the high-level architecture (HLA) note. It is regenerated only if
the fingerprint - public structure plus product docs (README, `docs_paths`) - changed
(or no note exists yet); pushes that touched neither a Python file nor a product doc
skip even the fingerprint check. The model writes the note from the source code, the
product docs and the folder layout in two steps, because a small model writes only
about a page per call:

1. one call per package (folder with a public API) with the package's full source
   code - or its outline if the source does not fit `max_prompt_chars` - for
   responsibility, how it works, key functions and notable details;
2. one call for overview, architecture, processes, features and scope from these
   package summaries, the product docs and the folder layout.

The **module list per package and the module map are drawn without the model** (from
docstrings, public names and the import graph), so they are complete and cannot
contain invented modules or edges.

`mode: overview` skips stage 1 and writes only this note; `mode: per-file` skips stage 3.

## Merge flow (`merge/task.py`, `merge/docs_check.py`)

Purpose: keep the product docs (`README.md`, `docs_paths`) up to date after every merge
into `main`. `docdrift copilot-task` runs in GitHub Actions; there is no LLM on the runner.

1. **Range:** `git merge-base(base, head)..head` - exactly the PR's changes, even if
   `main` moved on, and the same for merge, squash and rebase merges.
2. **Filter:** `ignore`, `docs_paths`, `docs/adr/` and `.github/` are left out. If
   nothing is left, there is no task - so Copilot's docs PR cannot trigger the next
   one. PRs from `copilot/*` branches are skipped as well.
3. **Pre-check** (`merge/docs_check.py`) collects *hints* that users could notice the change:

   | Hint | Example |
   |---|---|
   | public API of a public module changed | new parameter, removed function, rename |
   | public module added or removed | `pkg/new.py` |
   | packaging manifest changed | `pyproject.toml` (entry points, Python version) |
   | file without structural analysis changed | templates, YAML, shell (not `.gitignore`, `LICENSE`, lock files) |
   | commit message `feat:` or breaking | `feat: recursive mode`, `fix!:` |
   | the docs mention changed code | `` `work(path)` `` in `docs/usage.md`, even if only the body changed |

   Mentions count for symbols inside inline code, as a call `name(` or anywhere in a
   fenced code block, and for file names anywhere. Private names (`_x`) and private
   modules are not hints. No hint -> no issue (`github_docs.precheck: false` disables
   the check).
4. **Issue:** "check and update the product documentation" with the hints, the exact
   `file:line` mentions, the doc files in scope, the PR description, commit messages,
   changed files and AST changes. Copilot is told to change nothing - and say so - if
   the docs are still correct.
5. **Scope:** the issue states that only product docs are in scope - no architecture,
   internals, design rationale or ADRs, and no edits in `docs/adr/` (hand-written
   technical docs that some repositories keep there are never treated as product docs).
6. The workflow posts the issue with a user token so Copilot can be assigned
   (see `copilot-merge-setup.md`).

## State and failures

`.docdrift/state/<repo>.json` stores the last documented commit, the architecture
fingerprint and the ADR list. The commit only advances when every stage succeeded;
otherwise the range is retried next time. Retries are cheap: each note stores the
`source_hash` of the content it describes, and current notes are skipped without an
LLM call (`--force` overrides). ADR drafts are not duplicated and never created for
the initial import. If the LLM server is unreachable, the run stops after the first
file. `.docdrift/state/run.lock` prevents parallel runs.

Where a run starts: the last documented commit if it is an ancestor of `HEAD`; the
merge base if it lies on another branch; Git's empty tree if there is no state or the
commit no longer exists (rebase + gc).

Replies of the model are checked before anything is written: reasoning blocks
(`<think>`) are removed, and an empty or truncated reply (`finish_reason: length`) is
an error - it never overwrites a note.

## Known limitations

- Structural analysis covers Python only; other languages get the plain diff.
- Notes of deleted files are marked, not removed; renames show up as delete + add.
- The local flow describes the branch you push; notes of a branch you switch away from
  are updated on that branch's next push.
- The merge flow cannot verify what Copilot writes; the pull request review does.
- Overloads and property setters share a name, so only the last is tracked.
- Wikilinks between per-file notes depend on the model; the module map does not.
