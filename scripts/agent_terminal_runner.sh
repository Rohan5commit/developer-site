#!/bin/bash
set -euo pipefail

CONFIG_FILE="${MODAL_AGENT_CONFIG_FILE:-$HOME/.config/modal-agent-runner/config.env}"
GLOBAL_TASK_FILE="${MODAL_AGENT_TASK_FILE:-$HOME/.local/share/modal-agent-runner/modal_remote_tasks.py}"

usage() {
  /bin/cat <<'USAGE'
Usage:
  modal-agent-runner -- <command> [args...]
  modal-agent-runner -c "<shell command>"
  modal-agent-runner "<shell command>"

Modes:
  1) Local repo mode (preferred): find scripts/modal_exec.sh from current directory tree.
  2) Remote repo mode: if MODAL_REMOTE_REPO_URL is set, run via global modal_remote_tasks.py.

Review-flow helpers (set in config.env or the current shell):
  MODAL_REMOTE_PUSH_BRANCH
  MODAL_REMOTE_OPEN_PR=1
  MODAL_REMOTE_PR_BASE
  MODAL_REMOTE_PR_TITLE
  MODAL_REMOTE_PR_BODY
  MODAL_REMOTE_PR_DRAFT
USAGE
}

if [[ -f "$CONFIG_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$CONFIG_FILE"
fi

find_repo_root() {
  if [[ -n "${MODAL_AGENT_REPO_ROOT:-}" ]]; then
    if [[ -x "${MODAL_AGENT_REPO_ROOT}/scripts/modal_exec.sh" ]] && [[ -f "${MODAL_AGENT_REPO_ROOT}/modal_tasks.py" ]]; then
      /bin/echo "${MODAL_AGENT_REPO_ROOT}"
      return 0
    fi
  fi

  local dir="$PWD"
  while true; do
    if [[ -x "${dir}/scripts/modal_exec.sh" ]] && [[ -f "${dir}/modal_tasks.py" ]]; then
      /bin/echo "${dir}"
      return 0
    fi
    if [[ "${dir}" == "/" ]]; then
      break
    fi
    dir="$(dirname "${dir}")"
  done

  return 1
}

is_truthy() {
  local value
  value="$(printf '%s' "${1:-}" | /usr/bin/tr '[:upper:]' '[:lower:]')"
  [[ "$value" == "1" || "$value" == "true" || "$value" == "yes" ]]
}

if [[ $# -eq 0 ]]; then
  usage
  exit 2
fi

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

if [[ "${1:-}" == "-c" || "${1:-}" == "--cmd" ]]; then
  shift
  if [[ $# -eq 0 ]]; then
    echo "Error: missing command after -c/--cmd" >&2
    exit 2
  fi
  cmd="$1"
  shift
  if [[ $# -gt 0 ]]; then
    echo "Error: extra arguments found after raw command string." >&2
    exit 2
  fi
else
  if [[ "${1:-}" == "--" ]]; then
    shift
  fi
  if [[ $# -eq 0 ]]; then
    echo "Error: missing command." >&2
    exit 2
  fi
  printf -v cmd '%q ' "$@"
  cmd="${cmd% }"
fi

if ROOT_DIR="$(find_repo_root)"; then
  MODAL_EXEC="${ROOT_DIR}/scripts/modal_exec.sh"
  cd "$ROOT_DIR"
  exec "$MODAL_EXEC" -c "$cmd"
fi

if [[ -n "${MODAL_REMOTE_REPO_URL:-}" ]]; then
  if [[ ! -f "$GLOBAL_TASK_FILE" ]]; then
    echo "Error: missing global modal task file: $GLOBAL_TASK_FILE" >&2
    echo "Run ./scripts/install_agent_runner.sh from a clone of your modal repo." >&2
    exit 2
  fi

  MODAL_PYTHON_BIN="${MODAL_PYTHON_BIN:-/usr/bin/python3}"
  MODAL_RUN_FLAGS="${MODAL_RUN_FLAGS-}"
  MODAL_CPU="${MODAL_CPU:-6}"
  MODAL_MEMORY_MB="${MODAL_MEMORY_MB:-14336}"
  MODAL_REMOTE_REPO_BRANCH="${MODAL_REMOTE_REPO_BRANCH:-main}"
  MODAL_REMOTE_PUSH="${MODAL_REMOTE_PUSH:-1}"
  MODAL_REMOTE_COMMIT_MESSAGE="${MODAL_REMOTE_COMMIT_MESSAGE:-modal remote update}"

  export MODAL_CPU
  export MODAL_MEMORY_MB

  modal_cmd=("$MODAL_PYTHON_BIN" -m modal run)
  if [[ -n "$MODAL_RUN_FLAGS" ]]; then
    read -r -a run_flag_parts <<< "$MODAL_RUN_FLAGS"
    modal_cmd+=("${run_flag_parts[@]}")
  fi

  modal_cmd+=("$GLOBAL_TASK_FILE" --repo-url "$MODAL_REMOTE_REPO_URL" --branch "$MODAL_REMOTE_REPO_BRANCH" --cmd "$cmd")
  if is_truthy "$MODAL_REMOTE_PUSH"; then
    modal_cmd+=(--push 1 --commit-message "$MODAL_REMOTE_COMMIT_MESSAGE")
  fi

  exec "${modal_cmd[@]}"
fi

echo "Error: could not locate a Modal task-runner repo from \$PWD (${PWD})." >&2
echo "Run from inside a clone containing scripts/modal_exec.sh and modal_tasks.py," >&2
echo "or set MODAL_REMOTE_REPO_URL for remote-only mode." >&2
echo "Optionally set MODAL_AGENT_REPO_ROOT to a specific local clone path." >&2
exit 2
