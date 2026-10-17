# Set up docdrift in your project

A step-by-step guide from zero to both flows. Part 1 is done once per machine,
Part 2 once per project for the technical docs in Obsidian, Part 3 once per project
for the product docs in the repository. Parts 2 and 3 are independent - use either
or both.

| Part | Where | Result |
|---|---|---|
| 1. Install | your machine, once | the `docdrift` command, a local LLM, a vault |
| 2. Local flow | each project, on your machine | notes in Obsidian on every `git push` |
| 3. Merge flow | each project, on GitHub | README/docs checked after every merged PR |

## Part 1 - install docdrift (once per machine)

**1. Prerequisites:** Python 3.10 or newer and Git. On Windows install
[Git for Windows](https://git-scm.com/download/win) - the pre-push hook is a Bash script
and runs in its Git Bash.

**2. Install docdrift** into an environment of its own, not into each project's
virtualenv - one installation serves all projects. The name `docdrift` on PyPI belongs
to a different project, so always install from Git and pin a release tag (`v<version>`,
see the releases on GitHub):

```bash
# recommended: pipx (install it once with: python -m pip install --user pipx && python -m pipx ensurepath)
pipx install "git+https://github.com/tomdevtech/docdrift.git@<tag>"

# or a dedicated virtualenv - Linux/macOS:
python -m venv ~/.venvs/docdrift
~/.venvs/docdrift/bin/pip install "git+https://github.com/tomdevtech/docdrift.git@<tag>"
# Windows (PowerShell):
python -m venv $HOME\.venvs\docdrift
& $HOME\.venvs\docdrift\Scripts\pip install "git+https://github.com/tomdevtech/docdrift.git@<tag>"

# or, while developing docdrift itself, from a clone:
pip install -e ".[dev]"
```

Check it: `docdrift --version` prints the version. If the command is not found, open a
new terminal (pipx changes `PATH`) or call it with its full path.

**3. Start a local LLM server** with a model loaded. Pick a model that fits
**completely** into your GPU memory - one that spills into RAM is many times slower.
Measured models and times: [runtime.md](runtime.md).

- **LM Studio:** download a model in the app. Then load it with enough context and
  start the server - in the app (load the model, set *Context Length*, start the server
  in the *Developer* view) or on the command line:

  ```bash
  lms ls                                         # models on disk
  lms load <model> --context-length 32768        # load with 32k tokens of context
  lms server start                               # http://localhost:1234/v1
  ```

- **Ollama:** `ollama pull <model>`; the server runs on `http://localhost:11434/v1`.
  Set the context with `OLLAMA_CONTEXT_LENGTH=32768` before starting Ollama.

The **model id** for the config is the name the server reports: `lms ps` (LM Studio),
`ollama list` (Ollama), or `http://localhost:1234/v1/models` in a browser.
`docdrift check` also lists the available ids if the configured one is wrong.

**4. Have an Obsidian vault.** For `access_mode: local` any existing folder works;
docdrift never creates the vault itself. The notes go to `<vault>/Projects/<repo>/`.

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
model: "<model id from step 1.3>"
max_prompt_chars: 60000            # ~3-4 characters per token; well below the context length
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

- `max_prompt_chars`: with 32k tokens of context, 60,000 characters leave room for the
  answer; with 8k tokens use about 12,000.
- `mode`: `both` = one note per file plus the architecture note `_Architecture.md`,
  `overview` = only the architecture note (minutes instead of half an hour),
  `per-file` = only the notes per file.
- `ignore`: files that never get a note of their own. README and `docs/` still feed
  the architecture note.

All keys: [README](../README.md#configuration).

**3. Check the setup:**

```bash
docdrift check
```

It tests the config, the vault path, the LLM server and model, whether the model
copes with `max_prompt_chars` (a test prompt, takes a moment) and the hook. Every line
should start with `[OK  ]`; fix every `[FAIL]` before you go on - the message says what
is wrong.

**4. First run in the foreground** - it documents the whole repository:

```bash
docdrift run                       # or: docdrift run --mode overview
```

It prints `[n/total] <file>: documented` per file and ends with
`Done. N documented, ...`. Then open `<vault>/Projects/<repo_name>/` in Obsidian: one
note per file and `_Architecture.md`. A large repository can take a while with
`both` (see [runtime.md](runtime.md)); an interrupted run continues where it stopped.

**5. From now on just `git push`.** The hook runs docdrift in the background and only
documents what changed; the push is never delayed or blocked. The log is
`.docdrift/logs/docdrift.log`. `DOCDRIFT_SYNC=1 git push` runs it in the foreground,
`DOCDRIFT_SKIP=1 git push` skips it. `docdrift status` shows the mode, model, vault
target and last documented commit.

Write your own remarks below `<!-- AUTO-DOC-END -->` in a note - they survive every
regeneration. More: [usage.md](usage.md).

## Part 3 - merge flow: product docs in the repository (once per project)

After every pull request merged into the main branch, docdrift checks whether users
could notice the change and, if so, has `README.md` and `docs/` checked and updated in
a pull request. Details: [copilot-merge-setup.md](copilot-merge-setup.md).

**1. Install the workflow:**

```bash
docdrift init-github-product       # writes .github/workflows/product-docs-on-merge.yml
```

The workflow reacts to merges into `main`. If your main branch has another name, change
`branches: [main]` near the top of the workflow file.

**2. Choose the engine** in `.docdrift/config.yaml`:

```yaml
github_docs:
  enabled: true
  precheck: true                   # skip merges users cannot notice
  engine: "llm"                    # copilot | llm
  llm_api_base_url: "https://openrouter.ai/api/v1"   # engine llm: any OpenAI-compatible API
  llm_model: "openrouter/free"     # engine llm: OpenRouter picks a free model
  docs_paths:                      # what counts as product docs
    - "docs/"
    - "README.md"
```

| Engine | Needs | What you get |
|---|---|---|
| `copilot` | Copilot Pro, Pro+, Business or Enterprise | an issue for the Copilot coding agent, which opens the PR |
| `llm` | an API key, e.g. a free OpenRouter account | the workflow edits the docs with the LLM and opens the PR |

The `llm` engine sends the pre-check's hints, the AST changes and the doc files to the
provider - never the source code.

**3. Engine `llm` with OpenRouter** (skip for `copilot`):

1. Sign up at [openrouter.ai](https://openrouter.ai) - the free models need no credit
   card.
2. *Settings > Privacy* ([openrouter.ai/settings/privacy](https://openrouter.ai/settings/privacy)):
   many free models are served only by providers that may use the prompts. If the
   workflow later fails with "No endpoints found matching your data policy", allow those
   free endpoints here, or pin a free model whose provider does not train on inputs
   (`llm_model`).
3. *Keys > Create key*, copy the key.
4. In the GitHub repository: *Settings > Secrets and variables > Actions > Secrets >
   New repository secret*, name `DOCDRIFT_LLM_API_KEY`, value: the key.
5. *Settings > Actions > General > Workflow permissions*: enable **Allow GitHub Actions
   to create and approve pull requests**, *Save*.

For Groq, Gemini or a local LM Studio, set `llm_api_base_url` and `llm_model` as listed
in [copilot-merge-setup.md](copilot-merge-setup.md#engine-llm-in-detail).

**4. Engine `copilot`** (skip for `llm`): enable the Copilot coding agent for the
repository (github.com/settings/copilot) and store a fine-grained personal access token
of your account (repository access: this repo; Actions, Contents, Issues, Pull
requests = read and write) as the secret `DOCDRIFT_COPILOT_TOKEN`.

**5. Tell the workflow where to install docdrift from:** *Settings > Secrets and
variables > Actions > Variables > New repository variable*, name `DOCDRIFT_PIP_SPEC`,
value `git+https://github.com/tomdevtech/docdrift.git@<tag>` (the same tag as in Part 1).
Only the docdrift repository itself can skip this - it installs its own checkout.

**6. Commit and push** `.github/workflows/product-docs-on-merge.yml` and
`.docdrift/config.yaml` - the workflow reads the config. The state and log folders
stay ignored. The config contains your local `vault_path`; it is only used on your
machine.

**7. Test it:** merge a pull request with a user-visible change (best with `feat:` in
the title). Under *Actions* the run "docdrift - product docs on merge" lists the
pre-check's hints in the step "Build the docs task" and ends with:

- `copilot`: an issue assigned to Copilot, which opens a pull request a few minutes later;
- `llm`: a pull request from `docdrift/docs-pr-<n>`, or a comment "No documentation
  update needed" on the merged pull request.

A merge without user-visible changes ends with "Nothing to do" - also correct.
Errors and their fixes: [usage.md](usage.md#troubleshooting).

## Updating docdrift

1. Install the new version as in Part 1 (`pipx install --force "git+...@<new tag>"`).
2. In every project run `docdrift init` again - the hook stores the path of the Python
   interpreter docdrift is installed in. Your config is kept.
3. For the merge flow: run `docdrift init-github-product` again (it overwrites the
   workflow with the new version - re-apply a changed branch name), raise the tag in
   `DOCDRIFT_PIP_SPEC`, commit.
4. Read the [changelog](../CHANGELOG.md) for new or removed config keys; new keys are
   optional and their defaults apply until you set them. Compare with
   [the example config](../examples/docdrift.config.example.yaml).

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
