# Obsidian REST API setup

This setup is required when using `obsidian.access_mode: rest_api`, for
example when a GitHub-hosted Actions runner executes the documentation run
instead of a self-hosted runner or the local Git hook.

> TODO: Add step-by-step instructions after testing the actual setup.

## Planned steps

1. Install and enable the **Local REST API** community plugin in Obsidian.
2. Generate an API key in the plugin and store it securely, for example as a
   GitHub secret. Never put it in `.docdrift/config.yaml` as plain text.
3. The default endpoint (`127.0.0.1:27124`) is accessible only locally. Set up
   a tunnel such as **Tailscale** (recommended because it exposes no public
   internet port) instead of ngrok or Cloudflare Tunnel.
4. When using GitHub Actions, add the Tailscale GitHub Action so the runner is
   on the same private network.
5. Set `rest_api_base_url` in `.docdrift/config.yaml` to the Tailscale host name.
6. Test the connection. Obsidian must be open and running, or the endpoint will
   not be available.

## Known limitation

This option works only while Obsidian is running. For a more reliable,
always-available approach, use `access_mode: local` with a self-hosted runner
(see `self-hosted-runner-setup.md`).
