# Self-hosted GitHub Actions runner setup

As an alternative to `obsidian.access_mode: rest_api`, a self-hosted runner
can run on your own computer or home server. It then has direct access to the
local LLM (Ollama/LM Studio) and the Obsidian vault, so no tunnel is required.
This also allows `access_mode: local` to work inside the GitHub Actions
workflow, rather than only from the `pre-push` hook.

> TODO: Add step-by-step instructions after testing the actual setup.

## Planned steps

1. Register the runner under Repository settings → Actions → Runners.
   GitHub provides a download and configuration script there.
2. Configure the runner as a background service so it continues to run when
   nobody is logged in.
3. In `src/docdrift/github_workflows/obsidian-docs.yml.template`, change
   `runs-on` from `ubuntu-latest` to a custom label such as
   `[self-hosted, docdrift]`.
4. Make sure Ollama/LM Studio and Obsidian run on the same machine on which the
   runner is registered.

## Trade-off

Compared with `rest_api`, the advantage is that no tunnel or additional API
key is needed. The disadvantage is that the workflow can run only while your
computer and the runner service are running. If either is unavailable, the
Actions run will stall or fail.
