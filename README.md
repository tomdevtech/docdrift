# docdrift

A plug-and-play tool that automatically documents your projects in Obsidian
using a local LLM (Ollama / LM Studio), triggered by `git push`.

> Status: 🚧 under development

## Concept

`docdrift` provides two independent documentation flows:

**Flow 1 — internal technical documentation (Obsidian).** Changed files are
identified and sent to a locally running LLM (Ollama/LM Studio) together with
a configurable "playbook". The corresponding documentation in the Obsidian
vault is then updated. You can choose between two trigger mechanisms (a local
`pre-push` hook or a GitHub Actions workflow) and two ways to access the vault
(direct file access or Obsidian's "Local REST API" plugin through a tunnel).
These options can be combined freely in `.docdrift/config.yaml`.

**Flow 2 — product documentation in the repository (Copilot).** When a pull
request is merged into `main`, a GitHub Actions workflow creates an issue and
assigns it to the GitHub Copilot coding agent. The agent asynchronously opens
a pull request containing the updated product documentation.

The two flows can be used independently.

## Installation

```bash
# From your local clone (editable install during development)
pip install -e /path/to/docdrift

# Once stable, install directly from GitHub
pip install git+https://github.com/<your-user>/docdrift.git
```

## Quick start

In any target project, run the commands for the flow or flows you want:

```bash
cd my-other-project

# Flow 1, local trigger (Git hook)
docdrift init

# Flow 1, GitHub trigger (Actions workflow instead of or in addition to the hook)
docdrift init-github-obsidian

# Flow 2 (Copilot product documentation after a merge)
docdrift init-github-product
```

`docdrift init` creates:

- `.docdrift/config.yaml` — configuration (see the example below)
- `.git/hooks/pre-push` — local trigger
- `.docdrift/state/` — tracks the last documented commit

Each `init-github-*` command creates the corresponding file under
`.github/workflows/`.

To run Flow 1 manually without pushing:

```bash
docdrift run
```

## Configuration

See [`examples/docdrift.config.example.yaml`](examples/docdrift.config.example.yaml)
for all available options, including the model, API endpoint, vault path, and
playbook selection by file type.

## Customizing playbooks

The default playbooks are stored in
[`src/docdrift/playbooks/`](src/docdrift/playbooks/). You can copy custom or
project-specific playbooks to `.docdrift/playbooks/` in the target project and
edit them there. Project-specific playbooks take precedence over the bundled
defaults.

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check src/
```

## License

MIT — see [LICENSE](LICENSE)
