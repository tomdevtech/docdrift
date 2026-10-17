# Detailed usage

## Prerequisites

1. Python >= 3.10 and Git.
2. A local OpenAI-compatible LLM server with a model loaded:
   - Ollama: `ollama pull qwen2.5-coder:14b` (endpoint `http://localhost:11434/v1`)
   - LM Studio: load a model and start the server (endpoint `http://localhost:1234/v1`)
3. An Obsidian vault (for `access_mode: local` simply a folder).

**Context length matters.** Prompts are limited by `max_prompt_chars` (about 4
characters per token). LM Studio often defaults to 4096 tokens; either raise the
model's context length to 8192+ or lower `max_prompt_chars` to about 6000.
Smaller models (7b) write good notes when given the structural analysis, but are
more likely to ignore the strict ADR/architecture formats - failed ADR drafts are
reported and can be retried with `docdrift adr`.

## First run

```bash
cd your-project
docdrift init            # .docdrift/config.yaml, pre-push hook, .gitignore entries
$EDITOR .docdrift/config.yaml
docdrift check           # config, vault path, LLM server + model, context size, hook
docdrift run --dry-run   # preview: which files, ADR, overview - no LLM call
docdrift run             # documents the whole repository on the first run
```

`docdrift status` shows at any time, without an LLM call, the mode, the model, where
the notes go and the last documented commit (and whether `HEAD` is ahead of it).

Do the first run in the foreground: it sends every file to the model, which can take
a while on a large repository (progress is shown as `[n/total]`). If it is interrupted
or some files fail, just run it again - notes that are already current are skipped.

Set `obsidian.vault_path` to the vault root. Notes are created below
`Projects/<repo>/`; if the path already ends in `Projects`, its parent is used as the
vault root, so you never get `Projects/Projects/<repo>`.

`mode` selects what is written: `both` (default) writes one note per file and the
HLA note `_Architecture.md`, `overview` only the HLA note (one LLM call per package
plus one - minutes instead of half an hour), `per-file` only the notes per file.
`docdrift run --mode <mode>` overrides it for one run; if that skips a stage the config
enables, the range stays open so the next regular run still does it.
`docdrift run --mode overview --force` rebuilds the HLA note even when nothing new was
committed.

### Preview a run: `--dry-run`

`docdrift run --dry-run` shows what a run would do, without an LLM call, without writing
to the vault and without changing the state - useful before the first run (does
`ignore` catch the right files?) and before a long one:

```
$ docdrift run --dry-run
Range 47c87085..92ec757c (mode: both)
Would document 2 file(s):
  src/app/cli.py
  src/app/core.py
Would skip 1 file(s):
  src/app/util.py (formatting/comments only)
ADR draft: below the threshold (score 1, threshold 4)
Architecture overview: would be regenerated
Dry run - nothing was sent to the LLM or written to the vault.
```

- **Would document:** files that would get a new note. **Would skip:** files with only
  formatting/comment changes, or whose note already describes the current content.
- **Would mark:** notes of deleted files that would get the "Deleted" warning.
- **ADR draft:** whether a draft would be written, with the significance score.
- **Architecture overview:** whether `_Architecture.md` would be regenerated, or why not.

It accepts the same options as a run - `--from`, `--to`, `--mode`, `--force` - and shows
exactly what that run would do. If the vault cannot be opened, notes that are already
current cannot be detected; the output says so and lists those files as "would
document".

Re-run `docdrift init` after upgrading or after moving the virtualenv: the hook calls
the Python interpreter docdrift is installed in, so it also works from IDEs and Git GUIs
without an activated venv (an existing foreign hook is saved as `pre-push.bak`).

## The pre-push hook

- runs `docdrift run` **in the background**; output goes to `.docdrift/logs/docdrift.log`
  (rotated at about 1 MB), the push is not delayed;
- `DOCDRIFT_SYNC=1 git push` runs in the foreground, `DOCDRIFT_SKIP=1 git push` skips;
- if a run is still active, the next one stops with "Another docdrift run is active";
- if the LLM server is down, the run stops after the first file and everything is
  retried on the next push.

## Branches, rebases, deleted files

- **Switching branches:** if the last documented commit is on another branch, the run
  starts at the merge base, so the notes describe the branch you push.
- **Rebase:** if the last documented commit is gone, all files are checked; notes whose
  content hash matches are skipped without an LLM call.
- **Deleted files:** their notes are kept (with your manual section) and get a
  "Deleted" warning plus `deleted:` in the front matter.

## Reading the results

```
Projects/<repo>/
  _Architecture.md        HLA: overview, architecture, processes, features, scope,
                          one section per package, module map (modes both / overview)
  ADR/Index.md            list of all ADRs
  ADR/0001-....md         ADR drafts (status: proposed)
  <path/to/file>.md       one note per documented file (modes both / per-file)
```

Write your own remarks **below** `<!-- AUTO-DOC-END -->`; they are kept.

## Product docs on merge

`docdrift init-github-product` installs the merge workflow (setup:
[copilot-merge-setup.md](copilot-merge-setup.md)). After each merge into `main`:

- **internal change** (bug fix inside an undocumented function, private code, tests):
  the workflow log says "Nothing to do" and lists why - no issue.
- **change users could notice**, with `engine: copilot`: an issue for Copilot with the
  hints and the exact doc lines that mention changed code. Copilot's pull request either
  updates the outdated parts or says "No documentation update needed" - then just close it.
- **change users could notice**, with `engine: llm`: the LLM (default: OpenRouter's free
  models) checks up to `llm_max_files` doc files and the workflow opens a pull request from
  `docdrift/docs-pr-<n>`, or comments "No documentation update needed" on the merged PR.

Tips: write `feat:` (or `!` for breaking) in the PR title when behaviour changes
behind an unchanged signature, so the pre-check notices. Put user-facing context in
the PR description; Copilot reads it. Only `README.md` and `docs_paths` are edited -
if `docs_paths` contains technical pages, narrow it (e.g. to `docs/user/`).

## ADR workflow

ADRs are technical documentation, so they live only in the vault and are written only
by the local model. The merge flow never touches them.

1. A push with a significant change creates `ADR/NNNN-title.md` (`status: proposed`).
2. Open it, replace the "(rationale not recorded - please confirm)" spots with the real
   reasons, fix the text and set `status: accepted` (or delete the note).
3. For a change below the threshold: `docdrift adr --from <sha-before-feature>`.

Tune `adr.threshold`: lower = more drafts. `docdrift run` logs the score of every run,
so you can see what a typical push scores before changing it.

## Custom playbooks

Copy a file from `src/docdrift/local/playbooks/` to `.docdrift/playbooks/`, set
`playbook_dir: ".docdrift/playbooks"` and edit it. Available: `base` (per-file rules),
`python`, `typescript`, `fallback`, `architecture`, `adr`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Could not reach a local LLM server" | Ollama/LM Studio not running or wrong `api_base_url` |
| "reply was cut off" / "context length exceeded" | context smaller than prompt + answer: raise it or lower `max_prompt_chars`; `docdrift check` tests it |
| Nothing happens after a push | look into `.docdrift/logs/docdrift.log`, or push with `DOCDRIFT_SYNC=1` |
| "Another docdrift run is active" | a run is still going; a crashed run's lock expires after 3 h (or delete `.docdrift/state/run.lock`) |
| Hook does not run at all on Windows | re-run `docdrift init` (older versions wrote the hook with CRLF) |
| "vault_path ... does not exist" | path typo - docdrift never creates a vault |
| A file is never documented | matches `ignore`, is binary, or only formatting/comments changed |
| `ADR draft failed` | model ignored the format; retry with `docdrift adr --from <sha>` or use a larger model |
| REST API errors | Obsidian must be open; see `obsidian-rest-api-setup.md` |
| Merge issue is not assigned to Copilot | secret `DOCDRIFT_COPILOT_TOKEN` missing, no Copilot Pro/Pro+/Business/Enterprise licence, or the coding agent is not enabled; without a licence use `engine: llm` |
| Merge workflow logs "Nothing to do" | docs PR (`copilot/*`, `docdrift/*`), docs-only change, or only internal changes (pre-check); the log lists the hints it found |
| Copilot opened a PR with "No documentation update needed" | the pre-check saw a hint, Copilot found the docs still correct - close the PR |
| `llm`: "LLM answered 429" | free rate limit reached; re-run the workflow later, lower `llm_max_files` or pin another `:free` model |
| `llm`: "No endpoints found matching your data policy" (OpenRouter) | the free model's providers may use prompts - allow free endpoints at openrouter.ai/settings/privacy, or pin another `:free` model |
| Merge workflow never starts | it reacts to merges into `main` only - change `branches: [main]` in the workflow if your main branch has another name |
| `llm`: "LLM answered 401" or "DOCDRIFT_LLM_API_KEY is not set" | secret missing or the key belongs to another provider than `llm_api_base_url` |
| `llm`: a file "skipped - the model gave no usable answer", or "no doc file got a usable answer" | the (free) model ignored the JSON format even when asked twice; re-run (`openrouter/free` may pick another model), or pin a model that follows instructions better in `llm_model` |
| `llm`: branch pushed, but no PR | allow GitHub Actions to create pull requests (Settings > Actions > General) |
