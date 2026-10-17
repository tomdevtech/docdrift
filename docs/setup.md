# Set up docdrift in your project

A step-by-step guide from zero to both flows. Part 1 is done once per machine,
Part 2 once per project for the technical docs in Obsidian, Part 3 once per project
for the product docs in the repository. Parts 2 and 3 are independent - use either
or both.

| Part | Where | Result |
|---|---|---|
| 1. Install | your machine, once | the `docdrift` command and a local LLM |
| 2. Local flow | each project, on your machine | notes in Obsidian on every `git push` |
| 3. Merge flow | each project, on GitHub | README/docs checked after every merged PR |

## Part 1 - install docdrift (once per machine)

**1. Install docdrift** into an environment of its own, not into each project's
virtualenv - one installation serves all projects. The name `docdrift` on PyPI belongs
to a different project, so always install from Git and pin a release tag (`v<version>`,
see the releases on GitHub) or a commit:

```bash
pipx install "git+https://github.com/tomdevtech/docdrift.git@<tag>"
# or, without pipx, a dedicated virtualenv:
python -m venv ~/.venvs/docdrift
~/.venvs/docdrift/bin/pip install "git+https://github.com/tomdevtech/docdrift.git@<tag>"
# or, while developing docdrift itself, from a clone:
pip install -e ".[dev]"
```

Check it: `docdrift --version`.

**2. Start a local LLM server** with a model loaded:

- LM Studio: download a model, load it, start the server (`http://localhost:1234/v1`).
- Ollama: `ollama pull <model>` (`http://localhost:11434/v1`).

Pick a model that fits **completely** into your GPU memory and set its context length
to at least 32k tokens. Measured models and times: [runtime.md](runtime.md).

**3. Have an Obsidian vault.** For `access_mode: local` any existing folder works;
docdrift never creates the vault itself.

## Part 2 - local flow: technical docs in Obsidian (once per project)

Run everything from the root of the project's Git repository.

**1. Initialise:**

```bash
docdrift init
```

This creates `.docdrift/config.yaml`, installs the pre-push hook and adds
`.docdrift/state/` and `.docdrift/logs/` to `.gitignore`. An existing foreign pre-push
hook is kept as `.git/hooks/pre-push.bak`.

**2. Edit `.docdrift/config.yaml`** - the keys that matter for the local flow:

```yaml
repo_name: "my-project"            # folder name in the vault: Projects/<repo_name>/
api_base_url: "http://localhost:1234/v1"
model: "<model id as the server lists it>"
max_prompt_chars: 60000            # ~4 characters per token; stay below the context length
language: "English"                # language of the notes, e.g. "German"
mode: "both"                       # both | overview | per-file
obsidian:
  access_mode: "local"
  vault_path: "C:\\Users\\me\\Vault"   # the vault root (a path ending in Projects works too)
ignore:
  - "tests/"
  - "docs/"
  - "*.md"
```

`mode` decides what is written: `both` = one note per file plus the architecture note
`_Architecture.md`, `overview` = only the architecture note (minutes instead of half an
hour), `per-file` = only the notes per file. All keys:
[README](../README.md#configuration).

**3. Check the setup:**

```bash
docdrift check
```

It tests the config, the vault path, the LLM server and model, whether the model
copes with `max_prompt_chars`, and the hook. Fix every `[FAIL]` before you go on.

**4. First run in the foreground** - it documents the whole repository:

```bash
docdrift run                       # or: docdrift run --mode overview
```

**5. From now on just `git push`.** The hook runs docdrift in the background and only
documents what changed; the log is `.docdrift/logs/docdrift.log`.
`DOCDRIFT_SYNC=1 git push` runs it in the foreground, `DOCDRIFT_SKIP=1 git push` skips it.
`docdrift status` shows the mode, model, vault target and last documented commit.

The notes are in `<vault>/Projects/<repo_name>/`. Write your own remarks below
`<!-- AUTO-DOC-END -->` - they survive every regeneration. More: [usage.md](usage.md).

## Part 3 - merge flow: product docs in the repository (once per project)

After every pull request merged into `main`, docdrift checks whether users could
notice the change and, if so, has `README.md` and `docs/` checked and updated in a pull
request. Details: [copilot-merge-setup.md](copilot-merge-setup.md).

**1. Install the workflow:**

```bash
docdrift init-github-product       # writes .github/workflows/product-docs-on-merge.yml
```

**2. Choose the engine** in `.docdrift/config.yaml`:

```yaml
github_docs:
  enabled: true
  precheck: true                   # skip merges users cannot notice
  engine: "github-models"          # copilot | github-models
  docs_paths:                      # what counts as product docs
    - "docs/"
    - "README.md"
```

| Engine | Needs | What you get |
|---|---|---|
| `copilot` | Copilot Pro, Pro+, Business or Enterprise | an issue for the Copilot coding agent, which opens the PR |
| `github-models` | nothing (free, rate-limited) | the workflow edits the docs with GitHub Models and opens the PR |

**3. GitHub settings** (repository **Settings**):

- **Both engines:** under *Secrets and variables > Actions > Variables* create
  `DOCDRIFT_PIP_SPEC` = `git+https://github.com/tomdevtech/docdrift.git@<tag>` -
  where the workflow installs docdrift from. Only the docdrift repository itself can
  skip this (it installs its own checkout).
- **`github-models`:** under *Actions > General > Workflow permissions* enable
  **Allow GitHub Actions to create and approve pull requests**.
- **`copilot`:** enable the Copilot coding agent for the repository
  (github.com/settings/copilot) and store a fine-grained personal access token of your
  account (Actions, Contents, Issues, Pull requests = read and write) as the secret
  `DOCDRIFT_COPILOT_TOKEN`.

**4. Commit and push** `.github/workflows/product-docs-on-merge.yml` and
`.docdrift/config.yaml` - the workflow reads the config. The state and log folders
stay ignored.

**5. Test it:** merge a pull request with a user-visible change (best with `feat:` in
the title). Under *Actions* the run "docdrift - product docs on merge" should end with
an issue (`copilot`), or a docs pull request or a comment "No documentation update
needed" (`github-models`).

## Updating docdrift

1. Install the new version as in Part 1 (`pipx install --force "git+...@<new tag>"`).
2. In every project run `docdrift init` again - the hook stores the path of the Python
   interpreter docdrift is installed in. Your config is kept.
3. For the merge flow: run `docdrift init-github-product` again (it overwrites the
   workflow with the new version), raise the tag in `DOCDRIFT_PIP_SPEC`, commit.
4. New config keys are optional; their defaults apply until you set them. Compare
   with [the example config](../examples/docdrift.config.example.yaml).

## Removing docdrift from a project

Delete `.git/hooks/pre-push` (restore `pre-push.bak` if there is one),
`.github/workflows/product-docs-on-merge.yml` and the `.docdrift/` folder. The notes in
the vault stay where they are.

## Optional: local flow in GitHub Actions instead of the hook

`docdrift init-github-obsidian` installs a workflow that runs the local flow on GitHub.
It needs either a self-hosted runner next to your LLM and vault
([self-hosted-runner-setup.md](self-hosted-runner-setup.md)) or Obsidian's Local REST
API with a tunnel ([obsidian-rest-api-setup.md](obsidian-rest-api-setup.md)). Both
guides are drafts and not tested end to end yet - the pre-push hook is the
recommended way.
