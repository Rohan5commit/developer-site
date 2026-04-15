# Modal Offload Toolkit

Modal Offload Toolkit is a small collection of utilities for pushing CPU-heavy work, generic CLI commands, and GitHub-only remote edits into Modal while keeping local developer machines thin.

## What is inside

- `primary_compute.py`: example Modal-backed heavy compute with daily budget tracking and local fallback.
- `modal_tasks.py`: generic command runner that snapshots repo changes and syncs them back from Modal.
- `scripts/modal_remote_tasks.py`: remote-only GitHub workflow helper that clones a repo in Modal, runs commands, optionally pushes to a branch, and can open a pull request.
- `scripts/*.sh`: shell shims and policy installers for routing terminal activity through Modal.
- `nestjs-fastify-boilerplate/`: Fastify-first NestJS starter with health and compute-planning endpoints.
- `nim-claude-setup/`: NVIDIA NIM bridge for Claude Code.

## Quick start

```bash
make setup
make auth
```

## Heavy compute in Modal

`primary_compute.py` keeps expensive CPU work inside `do_heavy_stuff(...)` and exposes `run_heavy(...)` with three modes:

- `auto`: use Modal until the daily budget is exhausted.
- `modal`: always run remotely.
- `local`: run locally.

Examples:

```bash
make heavy PAYLOAD='{"iterations":24000000,"workers":6}'
make heavy-modal PAYLOAD='{"iterations":24000000,"workers":6}'
make heavy-local PAYLOAD='{"iterations":24000000,"workers":6}'
make usage-show
```

## CLI offload

Send any shell command through the Modal task runner:

```bash
./scripts/modal_exec.sh -- rg "TODO" .
./scripts/modal_exec.sh -- python -m pytest -q
./scripts/modal_exec.sh -c "hostname && pwd"
```

Install the shell shims if you want repo-local command routing:

```bash
./scripts/install_modal_shims.sh
source ./scripts/activate_modal_only.sh
```

## GitHub-only remote workflow

The global runner supports a fully remote branch-and-PR flow for cases where you do not want a local checkout to mutate:

```bash
make agent-runner-install
/usr/bin/python3 -m modal secret create github-token GITHUB_TOKEN="$(gh auth token)" --force
```

Then configure `~/.config/modal-agent-runner/config.env`:

```bash
MODAL_REMOTE_REPO_URL="https://github.com/Rohan5commit/Modal-terminal-instructions.git"
MODAL_REMOTE_REPO_BRANCH="main"
MODAL_REMOTE_PUSH="1"
MODAL_REMOTE_PUSH_BRANCH="codex/my-change"
MODAL_REMOTE_OPEN_PR="1"
MODAL_REMOTE_PR_BASE="main"
MODAL_REMOTE_PR_DRAFT="1"
MODAL_REMOTE_PR_TITLE="[modal] My remote change"
```

When `MODAL_REMOTE_PUSH_BRANCH` is set, the Modal runner pushes to that branch instead of directly updating the checkout branch. If `MODAL_REMOTE_OPEN_PR=1`, it will create or reuse a pull request against `MODAL_REMOTE_PR_BASE`.

## NestJS Fastify starter

The sample app is no longer a hello-world stub. It now includes:

- `GET /`: service overview and quick links.
- `GET /health`: health status, uptime, and timestamp.
- `GET /api/compute/presets`: sample payloads.
- `POST /api/compute/plan`: validated compute planning endpoint with workload partitioning, execution recommendation, and preview checksum.
- `GET /docs-json`: generated OpenAPI JSON.

Run it locally from the subdirectory:

```bash
cd nestjs-fastify-boilerplate
npm ci
npm run start:dev
```

## CI

GitHub Actions now validates:

- Python unit checks and syntax compilation.
- Bash and zsh script parsing.
- NestJS lint, build, unit tests, and e2e tests.

This keeps future remote-only changes from silently regressing the sample projects.
