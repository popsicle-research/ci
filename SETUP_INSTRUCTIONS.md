# FMG Platform Setup Guide

This guide walks through connecting a real GitHub repository to the FMG CI/CD backend and preparing your local environment to execute pipelines inside Docker containers. The steps assume macOS on Apple Silicon (M1/M2) but notes are included for other Unix-like systems.

## 1. Prerequisites

1. **GitHub access**
   - A GitHub repository you own or administer.
   - A Personal Access Token (PAT) with `repo` scope if you need to clone private repositories.
2. **System tooling**
   - [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running. Enable the "Use Rosetta for x86/amd64 emulation" option if you must run non-arm64 images.
   - [Homebrew](https://brew.sh/) (optional but recommended for dependency installation).
   - Python 3.11 or later (`brew install python@3.11`).
   - [Poetry](https://python-poetry.org/docs/#installation) for dependency management (`curl -sSL https://install.python-poetry.org | python3 -`).
3. **Networking utilities**
   - For local development, an HTTPS tunnel such as [ngrok](https://ngrok.com/) or [Cloudflare Tunnel](https://www.cloudflare.com/products/tunnel/) so GitHub can reach your workstation.

## 2. Clone the FMG Backend

```bash
git clone https://github.com/schneiderl/fmg.git
cd fmg
```

If you already have the repository, pull the latest changes:

```bash
git fetch origin
git checkout main
git pull --ff-only origin main
```

## 3. Configure Environment Variables

Create a `.env` file at the project root (or export variables in your shell profile) with the following values:

```bash
# GitHub authentication used when cloning private repositories
export FMG_GITHUB_TOKEN="<your-personal-access-token>"

# Token used when publishing commit statuses back to GitHub
export GITHUB_TOKEN="<your-personal-access-token>"

# Directory where cloned workspaces are staged
export FMG_WORKSPACE_ROOT="$PWD/workspaces"

# Optional: URL the CLI should use to talk to a remote FMG server
export FMG_SERVER_URL="http://localhost:5000"
```

> **Tip:** Run `source .env` (or add these exports to `~/.zshrc`) before starting the backend.

## 4. Install Python Dependencies

```bash
poetry install
```

To run ad-hoc commands, either use `poetry run <command>` or activate the Poetry shell (`poetry shell`).

## 5. Prepare the Target GitHub Repository

1. Add a `.popsicle/ci.yml` that FMG can parse. A minimal example:
   ```yaml
   version: 2.1
   jobs:
     build:
       docker:
         - image: python:3.11
       steps:
         - checkout
         - run: pip install -r requirements.txt
         - run: pytest
   workflows:
     version: 2
     build_and_test:
       jobs:
         - build
   ```
2. Commit and push this file to the repository's default branch so the webhook can discover it.

## 6. Expose the Webhook Endpoint

1. Start your tunneling service and forward port 5000 to your local machine. Example with ngrok:
   ```bash
   ngrok http --hostname=<custom-subdomain>.ngrok.app 5000
   ```
2. Copy the generated HTTPS URL; GitHub requires HTTPS for webhooks.

## 7. Launch the FMG Webhook Service

Use the helper script to boot the Flask app:

```bash
./scripts/dev_up.sh
```

This runs `poetry run flask --app fmg.webhook.app:app run --reload` on port 5000. Ensure Docker Desktop stays running so job containers can start when pipelines trigger.

## 8. Register the GitHub Webhook

1. Navigate to **Settings → Webhooks** in your GitHub repository.
2. Click **Add webhook** and supply:
   - **Payload URL:** The HTTPS tunnel URL from step 6 suffixed with `/webhook` (e.g., `https://<subdomain>.ngrok.app/webhook`).
   - **Content type:** `application/json`.
   - **Secret:** (Optional) Define a secret and set `GITHUB_WEBHOOK_SECRET` in FMG once HMAC validation is implemented.
   - **Events:** Select **Just the push event**.
3. Save the webhook. GitHub will send a ping event; FMG will respond with `ignored` because only push events are processed.

## 9. Verify Docker Execution Capability

Before triggering real pipelines, confirm Docker works end-to-end:

```bash
docker info
poetry run python -m fmg.runner.diagnostics
```

The diagnostics helper (if not yet implemented, run `docker run --rm hello-world`) ensures the Docker daemon responds and you can pull public images.

## 10. Trigger a Pipeline

1. Push a new commit to the configured branch of your GitHub repository.
2. Watch the FMG logs in your terminal. You should see the webhook intake, repository cloning, and job execution flow.
3. Inspect the SQLite database (`data/fmg.db` by default) or forthcoming CLI/API commands to verify pipeline and job statuses. Use `poetry run pytest` to run the project's automated tests locally when developing changes.

## 11. Running the Test Suite

Any code or configuration updates should be validated via:

```bash
./scripts/test.sh
```

This wrapper executes `poetry run pytest` and should pass before deploying changes.

## 12. Maintenance Tips

- Periodically prune old workspaces (`rm -rf workspaces/*`) if pipelines fail before cleanup.
- Keep Docker images updated with `docker system prune` and `docker pull <image>`.
- For production deployment, place FMG behind a TLS-terminating proxy and persist the SQLite database on durable storage or migrate to a managed service.

Following these steps connects your GitHub repository to the FMG backend and ensures Docker-based job execution works reliably on your development machine.
