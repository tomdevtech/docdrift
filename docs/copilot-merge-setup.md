# Merge flow setup (Copilot coding agent)

The merge flow keeps the **product documentation** - `README.md` and everything in
`docs_paths` - in line with the code. After every pull request merged into `main`,
docdrift checks whether users could notice the change at all and, if so, asks the
GitHub Copilot coding agent to verify the docs and update what is outdated.
Copilot answers with a pull request, which you review like any other.

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
2. Enable the Copilot coding agent for the repository (requires a paid Copilot plan).
3. Create a personal access token **of a user who has Copilot** and store it as the
   repository secret `DOCDRIFT_COPILOT_TOKEN`. Fine-grained token: repository access to
   this repo, permissions *Actions, Contents, Issues, Pull requests* = read and write.
   GitHub only lets user tokens assign Copilot; the workflow's own `GITHUB_TOKEN`
   cannot. Without the secret the issue is still created, but unassigned - assign it
   to Copilot by hand.
4. Set the repository variable `DOCDRIFT_PIP_SPEC`, pinned to a tag or commit, e.g.
   `git+https://github.com/<you>/docdrift.git@v0.2.0`. Never plain `docdrift`: that
   name on PyPI belongs to a different project. Not needed in the docdrift
   repository itself: without the variable it installs its own checkout, i.e.
   exactly the merged code.
5. Commit `.docdrift/config.yaml` (the state folder stays in `.gitignore`) with
   `github_docs.enabled: true`. Adjust `docs_paths` if your docs live elsewhere.
   `github_docs.precheck: false` asks Copilot after every merge instead.

## Strict separation

Copilot writes product documentation only: what the project does and how to install,
configure and use it. The issue forbids technical documentation - architecture
descriptions, module internals, design rationale and ADRs - and edits in `docs/adr/`.
All of that is written by the local model into your Obsidian vault (local flow).
If your `docs/` folder also holds technical pages, narrow `docs_paths` to the user
docs (e.g. `docs/user/`) so they are neither in Copilot's scope nor searched.

## Known limitations

- The pre-check is generous on purpose: it only rules out changes users cannot notice.
  A hint does not mean the docs are wrong - Copilot decides, and may answer "no update
  needed". A behaviour change hidden inside an undocumented function is not detected;
  mention it with `feat:` in the commit or PR title, or use `precheck: false`.
- Mentions are found for Python symbols and file names; other languages only trigger
  the "cannot be analysed" hint.
- `ignore` patterns apply here too: a PR that only touches ignored files gets no task.
- Each issue costs Copilot usage.
