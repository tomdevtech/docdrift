# Merge flow setup (Copilot or GitHub Models)

The merge flow keeps the **product documentation** - `README.md` and everything in
`docs_paths` - in line with the code. After every pull request merged into `main`,
docdrift checks whether users could notice the change at all and, if so, has the docs
verified and updated. Who does that is set by `github_docs.engine`:

| | `copilot` (default) | `github-models` |
|---|---|---|
| **Who** | the GitHub Copilot coding agent | a model on GitHub Models, called by the workflow |
| **Needs** | Copilot Pro, Pro+, Business or Enterprise | nothing - free with rate limits |
| **How** | docdrift opens an issue, Copilot opens a PR | the workflow edits the docs and opens a PR |
| **No update needed** | Copilot says so in its PR (you close it) | a comment on the merged PR, no PR |
| **Strength** | an agent that can explore the whole repository | small find/replace edits, never whole files |

Either way you review a pull request like any other.

## What happens on a merge

1. The workflow checks out the merge commit and fetches the PR head
   (`refs/pull/<n>/head`, available even after the branch was deleted).
2. `docdrift copilot-task` computes the exact PR range (merge base to PR head) and
   runs the AST analysis. **No LLM runs on the GitHub runner.**
3. **Pre-check** - is there anything users could notice? Hints are: public API
   changed, public module added or removed, packaging manifest changed, a file that
   cannot be analysed changed (templates, YAML, shell, other languages), a `feat` or
   breaking commit, or the docs mention a symbol or file that changed. Without any
   hint (bug fix inside a function the docs do not mention, private code, tests,
   formatting) the run ends - no issue, no Copilot request.
4. Otherwise the workflow creates an issue and assigns it to Copilot. It contains the
   hints, the exact `file:line` places where the docs mention changed code, the list
   of doc files, the PR description, commit messages and the AST changes.
5. Copilot first **decides whether the docs are still correct**, then updates only
   what is outdated. If nothing needs to change, it changes no file and writes "No
   documentation update needed" with a reason per hint - you close that PR.

Nothing happens for PRs from Copilot itself (`copilot/*` branches) or PRs that only
touched documentation or ignored files - so Copilot's docs PR never triggers the
next task.

## Setup

1. Install the workflow: `docdrift init-github-product` (writes
   `.github/workflows/product-docs-on-merge.yml`).
2. For `engine: copilot`:
   - Enable the Copilot coding agent for the repository (requires a paid Copilot plan).
   - Create a personal access token **of a user who has Copilot** and store it as the
     repository secret `DOCDRIFT_COPILOT_TOKEN`. Fine-grained token: repository access
     to this repo, permissions *Actions, Contents, Issues, Pull requests* = read and
     write. GitHub only lets user tokens assign Copilot; the workflow's own
     `GITHUB_TOKEN` cannot. Without the secret - or if the assignment fails, e.g.
     without a Copilot licence - the issue is still created, but unassigned.
3. For `engine: github-models`:
   - Settings > Actions > General > Workflow permissions: enable **Allow GitHub
     Actions to create and approve pull requests**. Without it the docs branch is
     pushed, but you open the pull request by hand.
   - Nothing else: the workflow uses its own `GITHUB_TOKEN` with `models: read`.
4. Set the repository variable `DOCDRIFT_PIP_SPEC`, pinned to a tag or commit, e.g.
   `git+https://github.com/<you>/docdrift.git@v1.0.0`. Never plain `docdrift`: that
   name on PyPI belongs to a different project. Not needed in the docdrift
   repository itself: without the variable it installs its own checkout, i.e.
   exactly the merged code.
5. Commit `.docdrift/config.yaml` (the state folder stays in `.gitignore`) with
   `github_docs.enabled: true` and the `engine` of your choice. Adjust `docs_paths` if
   your docs live elsewhere. `github_docs.precheck: false` asks after every merge instead.

## Engine `github-models` in detail

The free tier of GitHub Models allows only about 8,000 input tokens per request, so
docdrift works file by file (`docdrift models-docs`, module `merge/models.py`):

1. It picks at most `models_max_files` doc files (default 3): files whose lines mention
   changed code first, then `README.md`.
2. Per file the model gets the pre-check's hints, the mentioning lines, the AST changes
   and the file itself, within `models_max_chars` (default 24,000 characters). A file
   that does not fit is skipped and listed in the summary.
3. The model answers with find/replace edits. An edit is applied only if its `find`
   text occurs **exactly once**; others are listed as "not applied". The model never
   writes whole files, creates files or sees files outside `docs_paths`.
4. With changes, the workflow pushes `docdrift/docs-pr-<n>` and opens a pull request
   whose description lists every file and reason. Without changes it comments on the
   merged pull request: "No documentation update needed", with the model's reasons.

The model is set by `models_model` (default `openai/gpt-4.1-mini`, a low-tier model with
the most free requests per day). Rate limits depend on your account and are listed in
the GitHub Models documentation; a failed request fails the workflow run, so you can
re-run it later.

## Strict separation

Both engines write product documentation only: what the project does and how to install,
configure and use it. The issue forbids technical documentation - architecture
descriptions, module internals, design rationale and ADRs - and edits in `docs/adr/`.
All of that is written by the local model into your Obsidian vault (local flow).
If your `docs/` folder also holds technical pages, narrow `docs_paths` to the user
docs (e.g. `docs/user/`) so they are neither in scope nor searched.

## Known limitations

- The pre-check is generous on purpose: it only rules out changes users cannot notice.
  A hint does not mean the docs are wrong - Copilot decides, and may answer "no update
  needed". A behaviour change hidden inside an undocumented function is not detected;
  mention it with `feat:` in the commit or PR title, or use `precheck: false`.
- Mentions are found for Python symbols and file names; other languages only trigger
  the "cannot be analysed" hint.
- `ignore` patterns apply here too: a PR that only touches ignored files gets no task.
- `github-models` sees only the chosen doc files, not the whole repository: a feature
  that belongs into a doc file without any mention is only added if that file is
  `README.md` or among the first `models_max_files`.
- Each task costs usage: Copilot requests (`copilot`) or free GitHub Models requests
  (`github-models`, one per checked doc file).
