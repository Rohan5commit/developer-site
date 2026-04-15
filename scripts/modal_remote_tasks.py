import json
import os
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

import modal

APP_NAME = os.getenv("MODAL_REMOTE_APP_NAME", "modal-remote-task-runner")
DEFAULT_REPO_URL = os.getenv("MODAL_REMOTE_REPO_URL", "")
DEFAULT_BRANCH = os.getenv("MODAL_REMOTE_REPO_BRANCH", "main")
DEFAULT_CMD = os.getenv("MODAL_DEFAULT_CMD", "echo modal-ready")
DEFAULT_PUSH = int(os.getenv("MODAL_REMOTE_PUSH", "0"))
DEFAULT_PUSH_BRANCH = os.getenv("MODAL_REMOTE_PUSH_BRANCH", "")
DEFAULT_COMMIT_MESSAGE = os.getenv("MODAL_REMOTE_COMMIT_MESSAGE", "modal remote update")
DEFAULT_OPEN_PR = int(os.getenv("MODAL_REMOTE_OPEN_PR", "0"))
DEFAULT_PR_BASE = os.getenv("MODAL_REMOTE_PR_BASE", DEFAULT_BRANCH)
DEFAULT_PR_TITLE = os.getenv("MODAL_REMOTE_PR_TITLE", "")
DEFAULT_PR_BODY = os.getenv("MODAL_REMOTE_PR_BODY", "")
DEFAULT_PR_DRAFT = int(os.getenv("MODAL_REMOTE_PR_DRAFT", "1"))

CPU = float(os.getenv("MODAL_CPU", "6"))
MEMORY_MB = int(os.getenv("MODAL_MEMORY_MB", str(14 * 1024)))
TIMEOUT_SECONDS = int(os.getenv("MODAL_TIMEOUT_SECONDS", str(60 * 60)))

REPO_PATH = "/root/repo"
DEFAULT_SECRET = modal.Secret.from_name("github-token")

image = modal.Image.debian_slim().apt_install("bash", "ca-certificates", "git")
app = modal.App(APP_NAME)


def _repo_url_with_token(repo_url: str) -> str:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        return repo_url
    if repo_url.startswith("https://x-access-token:"):
        return repo_url
    if repo_url.startswith("https://github.com/"):
        return repo_url.replace(
            "https://github.com/",
            f"https://x-access-token:{token}@github.com/",
            1,
        )
    return repo_url


def _public_repo_url(repo_url: str) -> str:
    if repo_url.startswith("https://x-access-token:") and "@github.com/" in repo_url:
        suffix = repo_url.split("@github.com/", 1)[1]
        return f"https://github.com/{suffix}"
    return repo_url


def _normalize_branch_name(branch: str) -> str:
    branch = branch.strip()
    if branch.startswith("refs/heads/"):
        return branch[len("refs/heads/") :]
    return branch


def _repo_slug_from_url(repo_url: str) -> tuple[str, str]:
    cleaned = _public_repo_url(repo_url).rstrip("/")
    if cleaned.startswith("git@github.com:"):
        path = cleaned.split(":", 1)[1]
    else:
        path = urlparse(cleaned).path.lstrip("/")
    if path.endswith(".git"):
        path = path[:-4]
    owner, name = path.split("/", 1)
    return owner, name


def _github_request(method: str, api_path: str, payload: dict[str, Any] | None = None) -> Any:
    token = os.getenv("GITHUB_TOKEN", "").strip()
    if not token:
        raise RuntimeError("Missing GITHUB_TOKEN in the github-token secret.")

    url = f"https://api.github.com/{api_path.lstrip('/')}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "modal-remote-task-runner/1.0",
    }
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(url=url, data=body, headers=headers, method=method)

    try:
        with urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except HTTPError as err:
        detail = err.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API request failed ({err.code}) for {api_path}: {detail}"
        ) from err
    except URLError as err:
        raise RuntimeError(f"Unable to reach GitHub API: {err}") from err


def _ensure_pull_request(
    repo_url: str,
    head_branch: str,
    base_branch: str,
    title: str,
    body: str,
    draft: bool,
) -> dict[str, Any]:
    owner, repo = _repo_slug_from_url(repo_url)
    head_query = quote(f"{owner}:{head_branch}", safe="")
    base_query = quote(base_branch, safe="")
    existing = _github_request(
        "GET",
        f"repos/{owner}/{repo}/pulls?state=open&head={head_query}&base={base_query}",
    )
    if isinstance(existing, list) and existing:
        return existing[0]

    payload: dict[str, Any] = {
        "title": title,
        "head": head_branch,
        "base": base_branch,
        "draft": draft,
    }
    if body:
        payload["body"] = body

    created = _github_request("POST", f"repos/{owner}/{repo}/pulls", payload)
    if not isinstance(created, dict):
        raise RuntimeError("GitHub API returned an unexpected PR response.")
    return created


@app.function(
    image=image,
    cpu=CPU,
    memory=MEMORY_MB,
    timeout=TIMEOUT_SECONDS,
    secrets=[DEFAULT_SECRET],
)
def run_remote_cmd(
    repo_url: str,
    cmd: str,
    branch: str = "main",
    push: bool = False,
    push_branch: str = "",
    commit_message: str = "modal remote update",
    open_pr: bool = False,
    pr_base: str = "main",
    pr_title: str = "",
    pr_body: str = "",
    pr_draft: bool = True,
) -> dict[str, Any]:
    if not repo_url:
        raise ValueError("repo_url is required.")

    env = os.environ.copy()
    env["IN_MODAL_TASK_RUNNER"] = "1"

    clone_branch = _normalize_branch_name(branch or "main") or "main"
    auth_repo_url = _repo_url_with_token(repo_url)
    clone_args = ["git", "clone", "--depth", "1", "--branch", clone_branch, auth_repo_url, REPO_PATH]
    subprocess.run(clone_args, check=True, env=env)

    subprocess.run(["bash", "-lc", cmd], check=True, cwd=REPO_PATH, env=env)

    result: dict[str, Any] = {
        "repo_url": _public_repo_url(repo_url),
        "source_branch": clone_branch,
        "changed": False,
        "pushed": False,
    }

    if not push:
        return result

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        check=True,
        cwd=REPO_PATH,
        env=env,
        capture_output=True,
        text=True,
    ).stdout.strip()

    if not status:
        return result

    result["changed"] = True

    git_name = os.getenv("GIT_AUTHOR_NAME", "Modal Agent Runner")
    git_email = os.getenv("GIT_AUTHOR_EMAIL", "modal-agent@users.noreply.github.com")
    subprocess.run(["git", "config", "user.name", git_name], check=True, cwd=REPO_PATH, env=env)
    subprocess.run(["git", "config", "user.email", git_email], check=True, cwd=REPO_PATH, env=env)
    subprocess.run(["git", "add", "-A"], check=True, cwd=REPO_PATH, env=env)
    subprocess.run(["git", "commit", "-m", commit_message], check=True, cwd=REPO_PATH, env=env)

    if auth_repo_url != repo_url:
        subprocess.run(["git", "remote", "set-url", "origin", auth_repo_url], check=True, cwd=REPO_PATH, env=env)

    target_branch = _normalize_branch_name(push_branch) or clone_branch
    subprocess.run(["git", "push", "origin", f"HEAD:{target_branch}"], check=True, cwd=REPO_PATH, env=env)

    commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        cwd=REPO_PATH,
        env=env,
        capture_output=True,
        text=True,
    ).stdout.strip()

    result.update(
        {
            "pushed": True,
            "target_branch": target_branch,
            "commit_sha": commit_sha,
        }
    )

    if open_pr:
        base_branch = _normalize_branch_name(pr_base) or clone_branch
        if target_branch == base_branch:
            raise RuntimeError(
                "open_pr requires MODAL_REMOTE_PUSH_BRANCH to differ from MODAL_REMOTE_PR_BASE."
            )

        pull_request = _ensure_pull_request(
            repo_url=repo_url,
            head_branch=target_branch,
            base_branch=base_branch,
            title=pr_title.strip() or f"[modal] {commit_message}",
            body=pr_body.strip(),
            draft=bool(pr_draft),
        )
        result["pull_request"] = {
            "number": pull_request.get("number"),
            "url": pull_request.get("html_url"),
            "draft": pull_request.get("draft"),
        }

    return result


@app.local_entrypoint()
def main(
    cmd: str = DEFAULT_CMD,
    repo_url: str = DEFAULT_REPO_URL,
    branch: str = DEFAULT_BRANCH,
    push: int = DEFAULT_PUSH,
    push_branch: str = DEFAULT_PUSH_BRANCH,
    commit_message: str = DEFAULT_COMMIT_MESSAGE,
    open_pr: int = DEFAULT_OPEN_PR,
    pr_base: str = DEFAULT_PR_BASE,
    pr_title: str = DEFAULT_PR_TITLE,
    pr_body: str = DEFAULT_PR_BODY,
    pr_draft: int = DEFAULT_PR_DRAFT,
) -> None:
    result = run_remote_cmd.remote(
        repo_url=repo_url,
        cmd=cmd,
        branch=branch,
        push=bool(push),
        push_branch=push_branch,
        commit_message=commit_message,
        open_pr=bool(open_pr),
        pr_base=pr_base,
        pr_title=pr_title,
        pr_body=pr_body,
        pr_draft=bool(pr_draft),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
