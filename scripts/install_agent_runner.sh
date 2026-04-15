#!/bin/bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="${SCRIPT_PATH%/*}"
if [[ "$SCRIPT_DIR" == "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="."
fi
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && /bin/pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && /bin/pwd)"

TARGET_DIR="${HOME}/.local/bin"
TARGET_PATH="${TARGET_DIR}/modal-agent-runner"
RUNNER_SOURCE_PATH="${ROOT_DIR}/scripts/agent_terminal_runner.sh"

GLOBAL_DATA_DIR="${HOME}/.local/share/modal-agent-runner"
GLOBAL_TASKS_PATH="${GLOBAL_DATA_DIR}/modal_remote_tasks.py"
GLOBAL_TASKS_SOURCE="${ROOT_DIR}/scripts/modal_remote_tasks.py"

CONFIG_DIR="${HOME}/.config/modal-agent-runner"
CONFIG_PATH="${CONFIG_DIR}/config.env"

DEFAULT_REPO_URL="$(git -C "$ROOT_DIR" remote get-url origin 2>/dev/null || true)"
DEFAULT_REPO_BRANCH="$(git -C "$ROOT_DIR" branch --show-current 2>/dev/null || true)"
if [[ -z "$DEFAULT_REPO_BRANCH" ]]; then
  DEFAULT_REPO_BRANCH="main"
fi

/bin/mkdir -p "$TARGET_DIR"
/bin/mkdir -p "$GLOBAL_DATA_DIR"
/bin/mkdir -p "$CONFIG_DIR"

if [[ -e "$TARGET_PATH" || -L "$TARGET_PATH" ]]; then
  /bin/rm -f "$TARGET_PATH"
fi
/bin/cp "$RUNNER_SOURCE_PATH" "$TARGET_PATH"
/bin/chmod +x "$TARGET_PATH"

if [[ ! -f "$GLOBAL_TASKS_SOURCE" ]]; then
  echo "Error: missing source file ${GLOBAL_TASKS_SOURCE}" >&2
  exit 2
fi
/bin/cp "$GLOBAL_TASKS_SOURCE" "$GLOBAL_TASKS_PATH"
/bin/chmod +x "$GLOBAL_TASKS_PATH"

if [[ ! -f "$CONFIG_PATH" ]]; then
  /bin/cat > "$CONFIG_PATH" <<EOF
# Modal agent runner defaults (created by install_agent_runner.sh)
MODAL_REMOTE_REPO_URL="${DEFAULT_REPO_URL}"
MODAL_REMOTE_REPO_BRANCH="${DEFAULT_REPO_BRANCH}"
MODAL_REMOTE_PUSH="1"
MODAL_REMOTE_COMMIT_MESSAGE="modal remote update"
MODAL_CPU="6"
MODAL_MEMORY_MB="14336"

# Safer GitHub review flow (optional)
MODAL_REMOTE_PUSH_BRANCH=""
MODAL_REMOTE_OPEN_PR="0"
MODAL_REMOTE_PR_BASE="${DEFAULT_REPO_BRANCH}"
MODAL_REMOTE_PR_TITLE=""
MODAL_REMOTE_PR_BODY=""
MODAL_REMOTE_PR_DRAFT="1"
EOF
fi

/bin/cat <<EOF
Installed global agent terminal runner:
  ${TARGET_PATH}
Installed global remote task file:
  ${GLOBAL_TASKS_PATH}
Config file:
  ${CONFIG_PATH}

Set this as your terminal command runner for each coding agent.
Examples:
  ${TARGET_PATH} -c "hostname"
  ${TARGET_PATH} -- ls -la

Remote-only mode (no local repo checkout):
  - Ensure MODAL_REMOTE_REPO_URL and MODAL_REMOTE_REPO_BRANCH are set in ${CONFIG_PATH}
  - Ensure Modal secret named github-token includes GITHUB_TOKEN

Safer GitHub branch/PR flow:
  - Set MODAL_REMOTE_PUSH_BRANCH to a non-default branch name
  - Set MODAL_REMOTE_OPEN_PR=1 to create or reuse a pull request
  - Optionally set MODAL_REMOTE_PR_BASE, MODAL_REMOTE_PR_TITLE, and MODAL_REMOTE_PR_BODY
EOF
