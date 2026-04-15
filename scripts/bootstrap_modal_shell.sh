#!/bin/bash
set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]}"
SCRIPT_DIR="${SCRIPT_PATH%/*}"
if [[ "$SCRIPT_DIR" == "$SCRIPT_PATH" ]]; then
  SCRIPT_DIR="."
fi
SCRIPT_DIR="$(cd "$SCRIPT_DIR" && /bin/pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && /bin/pwd)"
REPO_TAG="$(basename "$ROOT_DIR" | /usr/bin/tr -cs '[:alnum:]' '-')"

BEGIN_ENV="# >>> modal-offload env (${REPO_TAG}) >>>"
END_ENV="# <<< modal-offload env (${REPO_TAG}) <<<"
BEGIN_HOOK="# >>> modal-offload hook (${REPO_TAG}) >>>"
END_HOOK="# <<< modal-offload hook (${REPO_TAG}) <<<"
BEGIN_BASH="# >>> modal-offload bash (${REPO_TAG}) >>>"
END_BASH="# <<< modal-offload bash (${REPO_TAG}) <<<"

strip_block() {
  local file="$1"
  local begin="$2"
  local end="$3"
  local tmp_file="$4"
  /usr/bin/awk -v begin="$begin" -v end="$end" '
    $0 == begin {skip=1; next}
    $0 == end {skip=0; next}
    !skip {print}
  ' "$file" > "$tmp_file"
}

upsert_block() {
  local file="$1"
  local begin="$2"
  local end="$3"
  local block="$4"

  /usr/bin/touch "$file"
  local tmp_file
  tmp_file="$(/usr/bin/mktemp)"
  strip_block "$file" "$begin" "$end" "$tmp_file"

  if [[ -s "$tmp_file" ]]; then
    /bin/echo >> "$tmp_file"
  fi
  /bin/cat >> "$tmp_file" <<BLOCK
$block
BLOCK

  /bin/mv "$tmp_file" "$file"
}

env_block="$BEGIN_ENV
_modal_repo="$ROOT_DIR"
if [[ "\$PWD" == "\$_modal_repo" || "\$PWD" == "\$_modal_repo/"* ]]; then
  if [[ -d "\$_modal_repo/.modal-shims" ]]; then
    _modal_shims="\$_modal_repo/.modal-shims"
    _modal_path="\${PATH:-}"
    _modal_path=":\${_modal_path}:"
    _modal_path="\${_modal_path//:\$_modal_shims:/:}"
    _modal_path="\${_modal_path#:}"
    _modal_path="\${_modal_path%:}"
    export PATH="\$_modal_shims\${_modal_path:+:\$_modal_path}"
    unset _modal_path
    export MODAL_SHIMS_ACTIVE=1
    export MODAL_CPU="\${MODAL_CPU:-6}"
    export MODAL_MEMORY_MB="\${MODAL_MEMORY_MB:-14336}"
    export MODAL_RUN_FLAGS="\${MODAL_RUN_FLAGS-}"
    export MODAL_SYNC_BACK="\${MODAL_SYNC_BACK:-1}"
    export MODAL_ROUTED_BY_SHIMS=1
    export MODAL_REPO_ROOT="\$_modal_repo"

    _modal_exec_abs() {
      "\$MODAL_REPO_ROOT/scripts/modal_exec.sh" -- "\$1" "\${@:2}"
    }

    _modal_wrap_python() {
      local py_bin="\$1"
      shift
      if [[ "\${1:-}" == "-m" && "\${2:-}" == "modal" ]]; then
        command "\$py_bin" "\$@"
        return
      fi
      _modal_exec_abs "\${py_bin##*/}" "\$@"
    }

    function /bin/ls() { _modal_exec_abs ls "\$@"; }
    function /bin/cat() { _modal_exec_abs cat "\$@"; }
    function /bin/rm() { _modal_exec_abs rm "\$@"; }
    function /bin/mv() { _modal_exec_abs mv "\$@"; }
    function /bin/cp() { _modal_exec_abs cp "\$@"; }
    function /bin/mkdir() { _modal_exec_abs mkdir "\$@"; }
    function /bin/touch() { _modal_exec_abs touch "\$@"; }
    function /bin/hostname() { _modal_exec_abs hostname "\$@"; }
    function /usr/bin/grep() { _modal_exec_abs grep "\$@"; }
    function /usr/bin/find() { _modal_exec_abs find "\$@"; }
    function /usr/bin/sed() { _modal_exec_abs sed "\$@"; }
    function /usr/bin/awk() { _modal_exec_abs awk "\$@"; }

    function /usr/bin/python3() { _modal_wrap_python /usr/bin/python3 "\$@"; }
    function /usr/bin/python() { _modal_wrap_python /usr/bin/python "\$@"; }
    function /opt/homebrew/bin/python3() { _modal_wrap_python /opt/homebrew/bin/python3 "\$@"; }
    function /opt/homebrew/bin/python() { _modal_wrap_python /opt/homebrew/bin/python "\$@"; }
    function /usr/local/bin/python3() { _modal_wrap_python /usr/local/bin/python3 "\$@"; }
    function /usr/local/bin/python() { _modal_wrap_python /usr/local/bin/python "\$@"; }
    function /opt/homebrew/bin/rg() { _modal_exec_abs rg "\$@"; }
    function /usr/local/bin/rg() { _modal_exec_abs rg "\$@"; }

    unset _modal_shims
  fi
fi
unset _modal_repo
$END_ENV"

hook_block="$BEGIN_HOOK
_modal_repo="$ROOT_DIR"
_modal_apply_repo_shims() {
  if [[ "\$PWD" == "\$_modal_repo" || "\$PWD" == "\$_modal_repo/"* ]]; then
    if [[ -d "\$_modal_repo/.modal-shims" ]]; then
      _modal_shims="\$_modal_repo/.modal-shims"
      _modal_path="\${PATH:-}"
      _modal_path=":\${_modal_path}:"
      _modal_path="\${_modal_path//:\$_modal_shims:/:}"
      _modal_path="\${_modal_path#:}"
      _modal_path="\${_modal_path%:}"
      export PATH="\$_modal_shims\${_modal_path:+:\$_modal_path}"
      unset _modal_path
      export MODAL_SHIMS_ACTIVE=1
      export MODAL_CPU="\${MODAL_CPU:-6}"
      export MODAL_MEMORY_MB="\${MODAL_MEMORY_MB:-14336}"
      export MODAL_RUN_FLAGS="\${MODAL_RUN_FLAGS-}"
      export MODAL_SYNC_BACK="\${MODAL_SYNC_BACK:-1}"
      export MODAL_ROUTED_BY_SHIMS=1
      export MODAL_REPO_ROOT="\$_modal_repo"
      unset _modal_shims
    fi
  fi
}
autoload -Uz add-zsh-hook 2>/dev/null || true
if typeset -f add-zsh-hook >/dev/null 2>&1; then
  add-zsh-hook chpwd _modal_apply_repo_shims
fi
_modal_apply_repo_shims
unset _modal_repo
$END_HOOK"

bash_block="$BEGIN_BASH
_modal_repo="$ROOT_DIR"
if [[ "\$PWD" == "\$_modal_repo" || "\$PWD" == "\$_modal_repo/"* ]]; then
  if [[ -d "\$_modal_repo/.modal-shims" ]]; then
    _modal_path="\${PATH:-}"
    _modal_path=":\${_modal_path}:"
    _modal_path="\${_modal_path//:\$_modal_repo/.modal-shims:/:}"
    _modal_path="\${_modal_path#:}"
    _modal_path="\${_modal_path%:}"
    export PATH="\$_modal_repo/.modal-shims\${_modal_path:+:\$_modal_path}"
    unset _modal_path
    export MODAL_SHIMS_ACTIVE=1
    export MODAL_CPU="\${MODAL_CPU:-6}"
    export MODAL_MEMORY_MB="\${MODAL_MEMORY_MB:-14336}"
    export MODAL_RUN_FLAGS="\${MODAL_RUN_FLAGS-}"
    export MODAL_SYNC_BACK="\${MODAL_SYNC_BACK:-1}"
    export MODAL_ROUTED_BY_SHIMS=1
    export MODAL_REPO_ROOT="\$_modal_repo"
  fi
fi
unset _modal_repo
$END_BASH"

"${ROOT_DIR}/scripts/install_modal_shims.sh"
upsert_block "${HOME}/.zshenv" "$BEGIN_ENV" "$END_ENV" "$env_block"
upsert_block "${HOME}/.zprofile" "$BEGIN_ENV" "$END_ENV" "$env_block"
upsert_block "${HOME}/.zshrc" "$BEGIN_HOOK" "$END_HOOK" "$hook_block"
upsert_block "${HOME}/.bashrc" "$BEGIN_BASH" "$END_BASH" "$bash_block"
upsert_block "${HOME}/.profile" "$BEGIN_BASH" "$END_BASH" "$bash_block"
upsert_block "${HOME}/.bash_profile" "$BEGIN_BASH" "$END_BASH" "$bash_block"

/bin/cat <<EOFMSG
Installed repo-scoped Modal shell bootstrap:
  ${HOME}/.zshenv
  ${HOME}/.zprofile
  ${HOME}/.zshrc
  ${HOME}/.bashrc
  ${HOME}/.profile
  ${HOME}/.bash_profile

This repo now auto-prepends:
  ${ROOT_DIR}/.modal-shims
for zsh/bash sessions started inside this repository.
EOFMSG
