import base64
import hashlib
import os
import posixpath
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

import modal

APP_NAME = os.getenv("MODAL_APP_NAME", "modal-task-runner")
REPO_PATH = "/root/repo"
DEFAULT_CMD = os.getenv("MODAL_DEFAULT_CMD", "echo modal-ready")
CPU = float(os.getenv("MODAL_CPU", "6"))
MEMORY_MB = int(os.getenv("MODAL_MEMORY_MB", str(14 * 1024)))
TIMEOUT_SECONDS = int(os.getenv("MODAL_TIMEOUT_SECONDS", str(60 * 60)))
SYNC_MAX_BYTES = int(os.getenv("MODAL_SYNC_MAX_BYTES", str(50 * 1024 * 1024)))

apt_packages = [
    pkg.strip()
    for pkg in os.getenv("MODAL_APT_PACKAGES", "bash,coreutils,findutils,git,grep,ripgrep,sed,zsh").split(",")
    if pkg.strip()
]
pip_packages = [
    pkg.strip()
    for pkg in os.getenv("MODAL_PIP_PACKAGES", "").split(",")
    if pkg.strip()
]

app = modal.App(APP_NAME)


def _ignore_local_path(path: Path) -> bool:
    ignored_parts = {
        ".git",
        ".modal-shims",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        ".gemini",
        ".vscode",
        ".caches",
        "node_modules",
        "dist",
        "coverage",
        "build",
        ".next",
        ".turbo",
    }
    ignored_names = {".DS_Store", "npm-debug.log"}
    ignored_suffixes = (".tsbuildinfo",)
    return (
        any(part in ignored_parts for part in path.parts)
        or path.name in ignored_names
        or any(path.name.endswith(suffix) for suffix in ignored_suffixes)
    )


image = modal.Image.debian_slim()
if apt_packages:
    image = image.apt_install(*apt_packages)
if pip_packages:
    image = image.pip_install(*pip_packages)
image = image.add_local_dir(".", remote_path=REPO_PATH, ignore=_ignore_local_path)


def _relative_repo_path(path: str) -> str:
    return path.replace(os.sep, "/")


def _snapshot_repo(root: str) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [name for name in dirnames if name != ".git"]
        for filename in filenames:
            abs_path = os.path.join(dirpath, filename)
            rel_path = _relative_repo_path(os.path.relpath(abs_path, root))
            st = os.lstat(abs_path)
            mode = st.st_mode & 0o777

            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(abs_path)
                digest = hashlib.sha256(f"symlink:{target}".encode("utf-8")).hexdigest()
                snapshot[rel_path] = {
                    "kind": "symlink",
                    "digest": digest,
                    "mode": mode,
                    "target": target,
                }
                continue

            if not stat.S_ISREG(st.st_mode):
                continue

            hasher = hashlib.sha256()
            with open(abs_path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                    hasher.update(chunk)

            snapshot[rel_path] = {
                "kind": "file",
                "digest": hasher.hexdigest(),
                "mode": mode,
            }
    return snapshot


def _collect_repo_changes(root: str, before: dict[str, dict[str, Any]]) -> dict[str, Any]:
    after = _snapshot_repo(root)
    removed = sorted(set(before.keys()) - set(after.keys()))

    updated: list[dict[str, Any]] = []
    payload_bytes = 0

    for rel_path in sorted(after.keys()):
        previous = before.get(rel_path)
        current = after[rel_path]
        if previous == current:
            continue

        abs_path = os.path.join(root, rel_path.replace("/", os.sep))
        if current["kind"] == "symlink":
            updated.append({"path": rel_path, "kind": "symlink", "target": current["target"]})
            continue

        with open(abs_path, "rb") as fh:
            content = fh.read()
        payload_bytes += len(content)
        if payload_bytes > SYNC_MAX_BYTES:
            raise RuntimeError(
                "sync_back payload exceeded MODAL_SYNC_MAX_BYTES "
                f"({SYNC_MAX_BYTES} bytes)."
            )

        updated.append(
            {
                "path": rel_path,
                "kind": "file",
                "mode": current["mode"],
                "content_b64": base64.b64encode(content).decode("ascii"),
            }
        )

    return {"updated": updated, "removed": removed}


def _validate_rel_path(rel_path: str) -> None:
    if not rel_path or rel_path.startswith("/"):
        raise ValueError(f"Invalid path: {rel_path!r}")
    parts = rel_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError(f"Unsafe path: {rel_path!r}")


def _apply_repo_changes(local_root: str, changes: dict[str, Any]) -> None:
    for rel_path in changes.get("removed", []):
        _validate_rel_path(rel_path)
        abs_path = os.path.join(local_root, rel_path.replace("/", os.sep))
        if os.path.islink(abs_path) or os.path.isfile(abs_path):
            os.remove(abs_path)
            continue
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)

    for item in changes.get("updated", []):
        rel_path = item["path"]
        _validate_rel_path(rel_path)
        abs_path = os.path.join(local_root, rel_path.replace("/", os.sep))
        parent = os.path.dirname(abs_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        if os.path.lexists(abs_path):
            if os.path.isdir(abs_path) and not os.path.islink(abs_path):
                shutil.rmtree(abs_path)
            else:
                os.remove(abs_path)

        if item["kind"] == "symlink":
            os.symlink(item["target"], abs_path)
            continue

        content = base64.b64decode(item["content_b64"].encode("ascii"))
        with open(abs_path, "wb") as fh:
            fh.write(content)
        os.chmod(abs_path, int(item["mode"]))


@app.function(image=image, cpu=CPU, memory=MEMORY_MB, timeout=TIMEOUT_SECONDS)
def run_cmd(cmd: str, workdir: str = REPO_PATH) -> None:
    env = os.environ.copy()
    env["IN_MODAL_TASK_RUNNER"] = "1"
    subprocess.run(["bash", "-lc", cmd], check=True, cwd=workdir, env=env)


@app.function(image=image, cpu=CPU, memory=MEMORY_MB, timeout=TIMEOUT_SECONDS)
def run_cmd_and_collect_changes(cmd: str, workdir: str = REPO_PATH) -> dict[str, Any]:
    env = os.environ.copy()
    env["IN_MODAL_TASK_RUNNER"] = "1"

    before = _snapshot_repo(REPO_PATH)
    subprocess.run(["bash", "-lc", cmd], check=True, cwd=workdir, env=env)
    return _collect_repo_changes(REPO_PATH, before)


@app.local_entrypoint()
def main(cmd: str = DEFAULT_CMD, workdir: str = ".", sync_back: bool = True) -> None:
    normalized_workdir = posixpath.normpath(posixpath.join(REPO_PATH, workdir))
    if normalized_workdir != REPO_PATH and not normalized_workdir.startswith(f"{REPO_PATH}/"):
        raise ValueError(f"Invalid workdir outside repo: {workdir}")

    if not sync_back:
        run_cmd.remote(cmd, normalized_workdir)
        return

    changes = run_cmd_and_collect_changes.remote(cmd, normalized_workdir)
    if changes.get("updated") or changes.get("removed"):
        _apply_repo_changes(os.getcwd(), changes)
