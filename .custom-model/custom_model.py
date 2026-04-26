#!/usr/bin/env python3
import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from modal.exception import InputCancellation
from modal.exception import TimeoutError as ModalTimeoutError

from modal_control import (
    BRIDGE_APP_NAME,
    INFERENCE_APP_NAME,
    call_modal_function,
    profile_for_workspace,
    workspace_for_profile,
)

APP_NAME = INFERENCE_APP_NAME
WEB_SEARCH_APP_NAME = os.getenv("MODAL_WEB_SEARCH_APP_NAME", "qwen-web-search")
TITLE = os.getenv("CUSTOM_MODEL_TITLE", "Custom model")
SESSION_FILE = Path(os.path.expanduser(os.getenv("CUSTOM_MODEL_SESSION_FILE", "~/.custom-model/session.json")))
MAX_SESSION_HISTORY = int(os.getenv("CUSTOM_MODEL_MAX_SESSION_HISTORY", "30"))
MAX_TOKENS_DEFAULT = int(os.getenv("MODAL_GPU_MAX_TOKENS_DEFAULT", "384"))
HISTORY_LIMIT_DEFAULT = int(os.getenv("MODAL_INFERENCE_HISTORY_LIMIT", "24"))
HARD_CAP_PRIMARY_SECONDS = float(os.getenv("MODAL_HARD_GPU_CAP_SECONDS_PRIMARY", "43200"))
HARD_CAP_BACKUP_SECONDS = float(os.getenv("MODAL_HARD_GPU_CAP_SECONDS_BACKUP", "43200"))
HARD_GPU_CAP_RESERVE_SECONDS = float(os.getenv("MODAL_HARD_GPU_CAP_RESERVE_SECONDS", "660"))
DEBUG = os.getenv("CUSTOM_MODEL_DEBUG", "0") == "1"
HARD_CAP_STOP_ALL_CONTAINERS = os.getenv("CUSTOM_MODEL_HARD_CAP_STOP_ALL_CONTAINERS", "1") == "1"
HARD_CAP_SHUTDOWN_TIMEOUT_SECONDS = float(os.getenv("CUSTOM_MODEL_HARD_CAP_SHUTDOWN_TIMEOUT_SECONDS", "45"))
HARD_CAP_STOP_RETRY_ATTEMPTS = int(os.getenv("CUSTOM_MODEL_HARD_CAP_STOP_RETRY_ATTEMPTS", "3"))
HARD_CAP_STOP_RETRY_SLEEP_SECONDS = float(os.getenv("CUSTOM_MODEL_HARD_CAP_STOP_RETRY_SLEEP_SECONDS", "2"))
HARD_CAP_FAIL_CLOSED_LOCK_ON_INCOMPLETE = os.getenv("CUSTOM_MODEL_FAIL_CLOSED_LOCK_ON_INCOMPLETE", "1") == "1"
AUTO_BUILD_MISSING_CACHE = os.getenv("CUSTOM_MODEL_AUTO_BUILD_MISSING_CACHE", "0") == "1"

def _resolve_workspace(value: str | None) -> str:
    normalized = (value or "primary").strip().lower()
    if normalized in {"backup", "secondary", "second", "2nd"}:
        return "backup"
    return "primary"


SELECTED_WORKSPACE = _resolve_workspace(os.getenv("CUSTOM_MODEL_WORKSPACE", "primary"))
DEFAULT_PROFILE = profile_for_workspace(SELECTED_WORKSPACE)
AUTO_FAILOVER_ENABLED = False
GPU_WAIT_NOTICE_SECONDS = float(os.getenv("CUSTOM_MODEL_GPU_WAIT_NOTICE_SECONDS", "15"))
GPU_WAIT_REPEAT_SECONDS = float(os.getenv("CUSTOM_MODEL_GPU_WAIT_REPEAT_SECONDS", "30"))
REASONING_MODE_DEFAULT = os.getenv("CUSTOM_MODEL_REASONING_MODE_DEFAULT", "on")
WEB_SEARCH_MODE_DEFAULT = os.getenv("CUSTOM_MODEL_WEB_SEARCH_MODE_DEFAULT", "on")
WEB_SEARCH_MAX_RESULTS = int(os.getenv("CUSTOM_MODEL_WEB_SEARCH_MAX_RESULTS", "10"))
WEB_SEARCH_MAX_FETCH_PAGES = int(os.getenv("CUSTOM_MODEL_WEB_SEARCH_MAX_FETCH_PAGES", "8"))
WEB_SEARCH_CONTEXT_CHAR_LIMIT = int(os.getenv("CUSTOM_MODEL_WEB_SEARCH_CONTEXT_CHAR_LIMIT", "5200"))
WEB_SEARCH_TIMEOUT_SECONDS = float(os.getenv("CUSTOM_MODEL_WEB_SEARCH_TIMEOUT_SECONDS", "12"))
WEB_SEARCH_URL = str(os.getenv("CUSTOM_MODEL_WEB_SEARCH_URL", "") or "").strip().rstrip("/")
WEB_SEARCH_TRANSPORT_DEFAULT = str(os.getenv("CUSTOM_MODEL_WEB_SEARCH_TRANSPORT", "auto") or "auto").strip().lower()
VM_SEARCH_SSH_HOST = str(os.getenv("CUSTOM_MODEL_VM_SSH_HOST", "") or "").strip()
VM_SEARCH_SSH_USER = str(os.getenv("CUSTOM_MODEL_VM_SSH_USER", "") or "").strip()
VM_SEARCH_SSH_KEY_PATH = os.path.expanduser(os.getenv("CUSTOM_MODEL_VM_SSH_KEY_PATH", ""))
VM_SEARCH_REMOTE_PORT = int(os.getenv("CUSTOM_MODEL_VM_SEARCH_REMOTE_PORT", "18781"))
VM_SEARCH_LOCAL_PORT = int(os.getenv("CUSTOM_MODEL_VM_SEARCH_LOCAL_PORT", "18777"))
INTERACTIVE_WARM_KEEPALIVE_ENABLED = os.getenv("CUSTOM_MODEL_INTERACTIVE_WARM_KEEPALIVE_ENABLED", "1") == "1"
INTERACTIVE_WARM_KEEPALIVE_INTERVAL_SECONDS = float(
    os.getenv("CUSTOM_MODEL_INTERACTIVE_WARM_KEEPALIVE_INTERVAL_SECONDS", "20")
)
INTERACTIVE_WARM_KEEPALIVE_TTL_SECONDS = float(
    os.getenv("CUSTOM_MODEL_INTERACTIVE_WARM_KEEPALIVE_TTL_SECONDS", "180")
)
INTERACTIVE_WARM_HOLD_UNTIL_EXIT = os.getenv("CUSTOM_MODEL_INTERACTIVE_WARM_HOLD_UNTIL_EXIT", "1") == "1"
WARM_REUSE_GRACE_SECONDS = float(os.getenv("CUSTOM_MODEL_WARM_REUSE_GRACE_SECONDS", "25"))
MODAL_CONFIG_PATH = os.path.expanduser(os.getenv("MODAL_CONFIG_PATH", "~/.custom-model/modal.toml"))
MODAL_BIN = os.path.expanduser(os.getenv("MODAL_BIN", "~/.local/bin/modal"))
PRIMARY_LIMIT_DONE_MESSAGE = "primary account limit done (12 hour done)"
BACKUP_LIMIT_DONE_MESSAGE = "backup account limit done (12 hour done)"
_HARD_CAP_SHUTDOWN_LOCK = threading.Lock()
AUTO_REASONING_KEYWORDS = (
    "debug",
    "bug",
    "fix",
    "why",
    "how do i",
    "how should i",
    "compare",
    "tradeoff",
    "trade-off",
    "plan",
    "design",
    "architecture",
    "algorithm",
    "step by step",
    "step-by-step",
    "reason",
    "analyze",
    "analysis",
    "root cause",
    "optimize",
    "strategy",
)
AUTO_WEB_KEYWORDS = (
    "latest",
    "current",
    "today",
    "yesterday",
    "tomorrow",
    "recent",
    "news",
    "version",
    "release",
    "released",
    "updated",
    "update",
    "docs",
    "documentation",
    "price",
    "pricing",
    "schedule",
    "deadline",
    "ceo",
    "president",
    "weather",
    "stock",
    "who is",
    "what is the current",
    "look up",
    "search the web",
    "find out",
    "check",
    "verify",
    "research",
    "investigate",
    "browse",
    "read the docs",
    "read docs",
    "what does",
    "according to",
)
ANSI_ENABLED = sys.stdout.isatty() and os.getenv("NO_COLOR", "").strip() == ""
COLOR_PRIMARY = "\033[38;5;111m"
COLOR_MUTED = "\033[38;5;244m"
COLOR_ASSISTANT = "\033[38;5;150m"
COLOR_ACCENT = "\033[1;38;5;81m"
COLOR_WARNING = "\033[38;5;214m"
COLOR_RESET = "\033[0m"


def _project_profiles() -> tuple[str, str]:
    return profile_for_workspace("primary"), profile_for_workspace("backup")


def _entry_matches_any_app(entry: dict, app_names: tuple[str, ...]) -> bool:
    haystack = " ".join(value for value in entry.values() if isinstance(value, str)).lower()
    return any(app_name and app_name.lower() in haystack for app_name in app_names)


def normalize_session_profile(profile: str) -> str:
    normalized = str(profile or DEFAULT_PROFILE).strip()
    if normalized in _project_profiles():
        return normalized
    return DEFAULT_PROFILE


def normalize_reasoning_mode(mode: str | None) -> str:
    normalized = str(mode or REASONING_MODE_DEFAULT).strip().lower()
    if normalized not in {"off", "auto", "on"}:
        return "auto"
    return normalized


def normalize_web_mode(mode: str | None) -> str:
    normalized = str(mode or WEB_SEARCH_MODE_DEFAULT).strip().lower()
    if normalized not in {"off", "auto", "on"}:
        return "auto"
    return normalized


def current_web_transport_label() -> str:
    transport = normalize_web_transport(WEB_SEARCH_TRANSPORT_DEFAULT)
    if use_direct_vm_search():
        return "vm-ssh" if _web_search_url_uses_localhost() else "vm"
    if transport == "direct" and not WEB_SEARCH_URL:
        return "vm-unset"
    return "modal-cpu"


def reasoning_status_line(mode: str) -> str:
    return style(f"[reasoning {normalize_reasoning_mode(mode)}]", COLOR_PRIMARY)


def web_status_line(mode: str) -> str:
    return style(
        f"[web {normalize_web_mode(mode)} via {current_web_transport_label()}]",
        COLOR_PRIMARY,
    )


def should_use_reasoning(prompt: str, mode: str) -> bool:
    normalized_mode = normalize_reasoning_mode(mode)
    if normalized_mode == "on":
        return True
    if normalized_mode == "off":
        return False

    compact = " ".join(str(prompt or "").strip().lower().split())
    if len(compact) >= 160:
        return True
    if compact.count("\n") >= 2:
        return True
    return any(keyword in compact for keyword in AUTO_REASONING_KEYWORDS)


def should_use_web_search(prompt: str, mode: str) -> bool:
    normalized_mode = normalize_web_mode(mode)
    if normalized_mode == "on":
        return True
    if normalized_mode == "off":
        return False

    compact = " ".join(str(prompt or "").strip().lower().split())
    if not compact:
        return False
    if "http://" in compact or "https://" in compact:
        return True
    if len(compact) >= 180:
        return True
    return any(keyword in compact for keyword in AUTO_WEB_KEYWORDS)


def now_local_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def style(text: str, color: str) -> str:
    if not ANSI_ENABLED:
        return text
    return f"{color}{text}{COLOR_RESET}"


def divider(char: str = "─", width: int = 64) -> str:
    return style(char * width, COLOR_MUTED)


def chat_label(title: str, session_id: str) -> str:
    trimmed_title = str(title or "").strip()
    if trimmed_title:
        return trimmed_title
    short_id = str(session_id or "").strip()
    return short_id[:8] if short_id else "unknown-chat"


def assistant_label() -> str:
    return TITLE


def print_assistant_message(content: str) -> None:
    print(style(f"{assistant_label()}:", COLOR_ASSISTANT))
    lines = str(content or "").splitlines() or [""]
    for line in lines:
        print(f"  {line}")


def print_chat_header(profile: str, reasoning_mode: str, web_mode: str, title: str, session_id: str) -> None:
    print(style(TITLE, COLOR_ACCENT))
    print(
        f"{usage_status_line(profile)} {reasoning_status_line(reasoning_mode)} "
        f"{web_status_line(web_mode)} [chat {chat_label(title, session_id)}]"
    )
    print(style("Type /help for commands.", COLOR_MUTED))
    print(divider())


def print_status_footer(profile: str, reasoning_mode: str, web_mode: str, title: str, session_id: str) -> None:
    print(divider())
    print(
        f"{usage_status_line(profile)} {reasoning_status_line(reasoning_mode)} "
        f"{web_status_line(web_mode)} [chat {chat_label(title, session_id)}]"
    )


def print_info(message: str) -> None:
    print(style(message, COLOR_MUTED))


def print_warning(message: str) -> None:
    print(style(message, COLOR_WARNING))


class StatusReporter:
    def __init__(self) -> None:
        self._done = threading.Event()
        self._phase = "preparing"
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def set_phase(self, phase: str) -> None:
        with self._lock:
            self._phase = phase

    def stop(self) -> None:
        self._done.set()
        self._thread.join(timeout=1)

    def _elapsed(self) -> int:
        return int(time.monotonic() - self._started_at)

    def _format_elapsed(self) -> str:
        elapsed = self._elapsed()
        minutes, seconds = divmod(elapsed, 60)
        return f"{minutes:02d}:{seconds:02d}"

    def _run(self) -> None:
        print("Preparing request...", file=sys.stderr, flush=True)
        if self._done.wait(GPU_WAIT_NOTICE_SECONDS):
            return
        last_phase = None
        while not self._done.is_set():
            with self._lock:
                phase = self._phase
            if phase != last_phase or phase in {"waiting_gpu", "preparing", "searching_web"}:
                if phase in {"waiting_gpu", "preparing"}:
                    print(
                        f"Waiting for GPU capacity... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "searching_web":
                    print(
                        f"Searching web... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "generating":
                    print(
                        f"Generating response... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "reasoning":
                    print(
                        f"Reasoning through request... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "reusing":
                    print(
                        f"Connecting to active model... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                elif phase == "saving":
                    print(
                        f"Saving chat state... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    print(
                        f"Still working... elapsed {self._format_elapsed()}",
                        file=sys.stderr,
                        flush=True,
                    )
                last_phase = phase
            if self._done.wait(GPU_WAIT_REPEAT_SECONDS):
                return


def call_inference(name: str, *args, preferred_profile: str, allow_failover: bool, **kwargs):
    return call_modal_function(
        APP_NAME,
        name,
        *args,
        preferred_profile=preferred_profile,
        allow_failover=allow_failover,
        **kwargs,
    )


def call_bridge(name: str, *args, preferred_profile: str, allow_failover: bool, **kwargs):
    return call_modal_function(
        BRIDGE_APP_NAME,
        name,
        *args,
        preferred_profile=preferred_profile,
        allow_failover=allow_failover,
        **kwargs,
    )


def call_web_search(name: str, *args, preferred_profile: str, allow_failover: bool, **kwargs):
    return call_modal_function(
        WEB_SEARCH_APP_NAME,
        name,
        *args,
        preferred_profile=preferred_profile,
        allow_failover=allow_failover,
        **kwargs,
    )


def cap_seconds_for_workspace(workspace_label: str) -> float:
    return HARD_CAP_BACKUP_SECONDS if workspace_label == "backup" else HARD_CAP_PRIMARY_SECONDS


def _hours(seconds: float) -> float:
    return seconds / 3600.0


def quota_status_for_profile(profile: str) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    payload, _ = call_bridge(
        "gpu_quota_status",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        preferred_profile=profile,
        allow_failover=False,
    )
    return payload


def usage_status_line(profile: str) -> str:
    workspace_label = workspace_for_profile(profile)
    cap_seconds = cap_seconds_for_workspace(workspace_label)
    try:
        quota = quota_status_for_profile(profile)
    except Exception:
        return style(f"[usage {workspace_label}: unavailable]", COLOR_MUTED)
    used_seconds = float(quota.get("used_seconds", 0.0))
    return style(f"[usage {workspace_label}: {_hours(used_seconds):.2f}/{_hours(cap_seconds):.2f}h]", COLOR_MUTED)


def _compact_text(text: str, max_chars: int) -> str:
    collapsed = " ".join(str(text or "").split())
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max(0, max_chars - 1)].rstrip() + "…"


def normalize_web_transport(value: str | None) -> str:
    normalized = str(value or WEB_SEARCH_TRANSPORT_DEFAULT).strip().lower()
    if normalized not in {"auto", "modal", "direct"}:
        return "auto"
    return normalized


def _parsed_web_search_url():
    return urllib.parse.urlparse(WEB_SEARCH_URL) if WEB_SEARCH_URL else None


def _web_search_url_uses_localhost() -> bool:
    parsed = _parsed_web_search_url()
    if parsed is None:
        return False
    return (parsed.hostname or "").strip().lower() in {"127.0.0.1", "localhost"}


def _is_tcp_port_open(host: str, port: int, timeout: float = 0.75) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _wait_for_local_port(host: str, port: int, timeout_seconds: float = 8.0) -> bool:
    deadline = time.time() + max(0.5, timeout_seconds)
    while time.time() < deadline:
        if _is_tcp_port_open(host, port):
            return True
        time.sleep(0.2)
    return _is_tcp_port_open(host, port)


def ensure_vm_search_tunnel() -> None:
    if not _web_search_url_uses_localhost():
        return
    parsed = _parsed_web_search_url()
    if parsed is None:
        raise RuntimeError("VM web search URL is not configured")
    local_host = (parsed.hostname or "127.0.0.1").strip() or "127.0.0.1"
    local_port = int(parsed.port or VM_SEARCH_LOCAL_PORT)
    if _is_tcp_port_open(local_host, local_port):
        return
    if not (VM_SEARCH_SSH_HOST and VM_SEARCH_SSH_USER and VM_SEARCH_SSH_KEY_PATH):
        raise RuntimeError(
            "VM web search tunnel is not configured; missing CUSTOM_MODEL_VM_SSH_HOST, "
            "CUSTOM_MODEL_VM_SSH_USER, or CUSTOM_MODEL_VM_SSH_KEY_PATH"
        )
    if not Path(VM_SEARCH_SSH_KEY_PATH).exists():
        raise RuntimeError(f"VM web search SSH key not found: {VM_SEARCH_SSH_KEY_PATH}")
    target = f"{VM_SEARCH_SSH_USER}@{VM_SEARCH_SSH_HOST}"
    command = [
        "ssh",
        "-f",
        "-N",
        "-o",
        "BatchMode=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "ConnectTimeout=10",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "IdentitiesOnly=yes",
        "-i",
        VM_SEARCH_SSH_KEY_PATH,
        "-L",
        f"{local_host}:{local_port}:127.0.0.1:{VM_SEARCH_REMOTE_PORT}",
        target,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or f"ssh exited with status {result.returncode}"
        raise RuntimeError(f"Failed to open VM web search tunnel: {details}")
    if not _wait_for_local_port(local_host, local_port):
        raise RuntimeError("VM web search tunnel did not become ready")


def use_direct_vm_search() -> bool:
    transport = normalize_web_transport(WEB_SEARCH_TRANSPORT_DEFAULT)
    if transport == "modal":
        return False
    if transport == "direct":
        return bool(WEB_SEARCH_URL)
    return bool(WEB_SEARCH_URL)


def direct_vm_search(query: str) -> dict[str, Any]:
    if not WEB_SEARCH_URL:
        raise RuntimeError("CUSTOM_MODEL_WEB_SEARCH_URL is not configured")
    ensure_vm_search_tunnel()
    params = urllib.parse.urlencode(
        {
            "q": query,
            "format": "json",
            "language": "en-US",
            "safesearch": 0,
            "categories": "general",
            "max_results": str(WEB_SEARCH_MAX_RESULTS),
            "max_fetch_pages": str(WEB_SEARCH_MAX_FETCH_PAGES),
        }
    )
    request = urllib.request.Request(
        f"{WEB_SEARCH_URL}/search?{params}",
        headers={
            "User-Agent": "CustomModel/1.0",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))
    raw_results = payload.get("results", [])
    results: list[dict[str, Any]] = []
    if isinstance(raw_results, list):
        for item in raw_results[:WEB_SEARCH_MAX_RESULTS]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            upstream_excerpt = str(
                item.get("content_excerpt") or item.get("snippet") or item.get("content") or ""
            ).strip()
            upstream_source = str(item.get("source") or "").strip()
            engines = item.get("engines")
            if not upstream_source and isinstance(engines, list):
                upstream_source = ", ".join(str(x) for x in engines if isinstance(x, str))
            results.append(
                {
                    "title": _compact_text(str(item.get("title") or "Untitled"), 180),
                    "url": url,
                    "snippet": _compact_text(upstream_excerpt, 320),
                    "content_excerpt": _compact_text(upstream_excerpt, 420),
                    "source": _compact_text(upstream_source, 80),
                }
            )
    upstream_backend = str(payload.get("backend") or "search").strip() or "search"
    if not upstream_backend.startswith("vm-"):
        upstream_backend = f"vm-{upstream_backend}"
    return {
        "query": str(payload.get("query") or query),
        "backend": upstream_backend,
        "results": results,
        "errors": payload.get("errors") if isinstance(payload.get("errors"), list) else [],
        "fetched_at": str(payload.get("fetched_at") or now_local_iso()),
        "configured_searxng": bool(payload.get("configured_searxng")),
        "fetched_pages": payload.get("fetched_pages"),
        "timing": payload.get("timing") if isinstance(payload.get("timing"), dict) else None,
    }


def search_web(query: str, preferred_profile: str) -> tuple[dict[str, Any], str]:
    if use_direct_vm_search():
        return direct_vm_search(query), "direct-vm"
    return call_web_search(
        "search_web",
        query,
        max_results=WEB_SEARCH_MAX_RESULTS,
        max_fetch_pages=WEB_SEARCH_MAX_FETCH_PAGES,
        preferred_profile=preferred_profile,
        allow_failover=False,
    )


def build_web_context_message(payload: dict[str, Any]) -> dict[str, str] | None:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None

    lines = [
        "Live web search context. Use this only when relevant to the user's request.",
        "Prefer this web context over stale memory for current facts.",
        "Answer in your own words using the evidence below. Do not just dump raw result titles or metadata.",
        "When you rely on this web context, mention the relevant URLs.",
        f"Query: {_compact_text(str(payload.get('query') or ''), 240)}",
        f"Fetched at: {str(payload.get('fetched_at') or '').strip()}",
        f"Backend: {str(payload.get('backend') or 'unknown').strip()}",
    ]
    for index, result in enumerate(results[:WEB_SEARCH_MAX_RESULTS], start=1):
        if not isinstance(result, dict):
            continue
        title = _compact_text(str(result.get("title") or "Untitled"), 180)
        url = _compact_text(str(result.get("url") or ""), 220)
        snippet = _compact_text(
            str(result.get("content_excerpt") or result.get("snippet") or ""),
            520,
        )
        source = _compact_text(str(result.get("source") or ""), 80)
        lines.append(f"{index}. {title}")
        if url:
            lines.append(f"   URL: {url}")
        if source:
            lines.append(f"   Source: {source}")
        if snippet:
            lines.append(f"   Notes: {snippet}")

    content = "\n".join(lines)
    if len(content) > WEB_SEARCH_CONTEXT_CHAR_LIMIT:
        content = content[: max(0, WEB_SEARCH_CONTEXT_CHAR_LIMIT - 1)].rstrip() + "…"
    return {"role": "system", "content": content}


def inject_web_context(messages: list[dict[str, str]], payload: dict[str, Any]) -> list[dict[str, str]]:
    web_message = build_web_context_message(payload)
    if web_message is None:
        return messages
    system_messages = [m for m in messages if str(m.get("role") or "").strip().lower() == "system"]
    dialogue_messages = [m for m in messages if str(m.get("role") or "").strip().lower() != "system"]
    return system_messages + [web_message] + dialogue_messages


def print_web_search_results(payload: dict[str, Any]) -> None:
    backend = str(payload.get("backend") or "unknown").strip()
    query = str(payload.get("query") or "").strip()
    print(style(f"Web search [{backend}]", COLOR_ASSISTANT))
    if query:
        print(f"  Query: {query}")
    timing = payload.get("timing")
    if isinstance(timing, dict):
        total_seconds = timing.get("total_seconds")
        search_seconds = timing.get("search_seconds")
        fetch_seconds = timing.get("fetch_seconds")
        fetched_pages = payload.get("fetched_pages")
        timing_bits = []
        if search_seconds is not None:
            timing_bits.append(f"search {search_seconds}s")
        if fetch_seconds is not None:
            timing_bits.append(f"read {fetch_seconds}s")
        if total_seconds is not None:
            timing_bits.append(f"total {total_seconds}s")
        if fetched_pages is not None:
            timing_bits.append(f"pages {fetched_pages}")
        if timing_bits:
            print(f"  Speed: {' | '.join(timing_bits)}")
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        print("  No results.")
        return
    for index, result in enumerate(results[:WEB_SEARCH_MAX_RESULTS], start=1):
        if not isinstance(result, dict):
            continue
        title = str(result.get("title") or "Untitled").strip()
        url = str(result.get("url") or "").strip()
        snippet = str(result.get("content_excerpt") or result.get("snippet") or "").strip()
        print(f"  {index}. {title}")
        if url:
            print(f"     {url}")
        if snippet:
            print(f"     {_compact_text(snippet, 240)}")


def run_direct_web_search(query: str, profile: str) -> None:
    query = str(query or "").strip()
    if not query:
        print_warning("Usage: /search <query>")
        return
    try:
        payload, _ = search_web(query, profile)
    except Exception as error:  # noqa: BLE001
        print_warning(f"Web search failed: {error}")
        return
    print_web_search_results(payload)


def maybe_correct_quota_after_generation(profile: str, quota_before: dict[str, object], gpu_started_at: float | None) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    quota_after = quota_status_for_profile(profile)
    before_used = float(quota_before.get("used_seconds", 0.0))
    after_used = float(quota_after.get("used_seconds", 0.0))

    if gpu_started_at is None or after_used > before_used + 0.001:
        return quota_after

    charged_seconds = max(0.0, time.monotonic() - gpu_started_at)
    if charged_seconds < 0.5:
        return quota_after

    corrected_used = before_used + charged_seconds
    corrected_quota, _ = call_bridge(
        "gpu_quota_set",
        workspace_label,
        corrected_used,
        cap_seconds_for_workspace(workspace_label),
        "local-client-correction",
        json.dumps(
            {
                "source": "custom_model.py",
                "reason": "server_finalize_zero",
                "previous_used_seconds": before_used,
                "charged_seconds": charged_seconds,
            }
        ),
        preferred_profile=profile,
        allow_failover=False,
    )
    return corrected_quota


def reserve_quota_for_generation(
    profile: str,
    reserve_seconds: float,
    reason: str,
    metadata_json: str,
) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    payload, _ = call_bridge(
        "gpu_quota_reserve",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        float(reserve_seconds),
        reason,
        metadata_json,
        preferred_profile=profile,
        allow_failover=False,
    )
    return payload


def finalize_quota_reservation(
    profile: str,
    reservation_id: str,
    actual_seconds: float,
    reason: str,
    metadata_json: str,
) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    payload, _ = call_bridge(
        "gpu_quota_finalize",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        reservation_id,
        float(max(0.0, actual_seconds)),
        reason,
        metadata_json,
        preferred_profile=profile,
        allow_failover=False,
    )
    return payload


def lock_workspace_quota(profile: str, reason: str, metadata_json: str) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    payload, _ = call_bridge(
        "gpu_quota_lock",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        reason,
        metadata_json,
        preferred_profile=profile,
        allow_failover=False,
    )
    return payload


def unlock_workspace_quota(profile: str, reason: str, metadata_json: str) -> dict[str, object]:
    workspace_label = workspace_for_profile(profile)
    payload, _ = call_bridge(
        "gpu_quota_unlock",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        reason,
        metadata_json,
        preferred_profile=profile,
        allow_failover=False,
    )
    return payload


def lock_all_profiles_fail_closed(reason: str, metadata_json: str) -> None:
    for profile in _project_profiles():
        try:
            lock_workspace_quota(profile, reason, metadata_json)
        except Exception as lock_error:  # noqa: BLE001
            print(
                f"WARNING: failed to fail-close lock workspace for profile={profile}: {lock_error}",
                file=sys.stderr,
                flush=True,
            )


def choose_generation_profile(preferred_profile: str, allow_failover: bool) -> str:
    workspace_label = workspace_for_profile(preferred_profile)
    quota, _ = call_bridge(
        "gpu_quota_status",
        workspace_label,
        cap_seconds_for_workspace(workspace_label),
        preferred_profile=preferred_profile,
        allow_failover=False,
    )
    remaining_seconds = quota.get("remaining_seconds")
    blocked_for_new_request = bool(quota["blocked"])
    if remaining_seconds is not None and remaining_seconds < HARD_GPU_CAP_RESERVE_SECONDS:
        blocked_for_new_request = True
    if not blocked_for_new_request:
        return preferred_profile

    if bool(quota.get("locked")):
        raise RuntimeError(
            "HARD_USAGE_CAP_REACHED "
            f"workspace={workspace_label} "
            "locked=1 "
            f"lock_reason={str(quota.get('lock_reason') or '').strip()} "
            f"used_seconds={float(quota.get('used_seconds', 0.0)):.3f} "
            f"cap_seconds={float(quota.get('cap_seconds', cap_seconds_for_workspace(workspace_label))):.3f}"
        )

    if allow_failover and workspace_label == "primary":
        backup_profile = profile_for_workspace("backup")
        backup_quota, _ = call_bridge(
            "gpu_quota_status",
            "backup",
            cap_seconds_for_workspace("backup"),
            preferred_profile=backup_profile,
            allow_failover=False,
        )
        backup_remaining_seconds = backup_quota.get("remaining_seconds")
        backup_blocked_for_new_request = bool(backup_quota["blocked"])
        if backup_remaining_seconds is not None and backup_remaining_seconds < HARD_GPU_CAP_RESERVE_SECONDS:
            backup_blocked_for_new_request = True
        if backup_blocked_for_new_request and bool(backup_quota.get("locked")):
            raise RuntimeError(
                "HARD_USAGE_CAP_REACHED "
                "workspace=backup "
                "locked=1 "
                f"lock_reason={str(backup_quota.get('lock_reason') or '').strip()} "
                f"used_seconds={float(backup_quota.get('used_seconds', 0.0)):.3f} "
                f"cap_seconds={float(backup_quota.get('cap_seconds', cap_seconds_for_workspace('backup'))):.3f}"
            )
        if not backup_blocked_for_new_request:
            return backup_profile

    enforce_hard_cap_shutdown("preflight-quota-blocked")
    raise RuntimeError(
        "HARD_USAGE_CAP_REACHED "
        f"workspace={workspace_label} "
        f"used_seconds={quota['used_seconds']:.3f} "
        f"cap_seconds={quota['cap_seconds']:.3f}"
    )


def hard_cap_user_message(error: BaseException) -> str | None:
    if not isinstance(error, RuntimeError):
        return None
    text = str(error)
    if not text.startswith("HARD_USAGE_CAP_REACHED"):
        return None
    if "workspace=primary" in text:
        return PRIMARY_LIMIT_DONE_MESSAGE
    if "workspace=backup" in text:
        return BACKUP_LIMIT_DONE_MESSAGE
    return "account limit done (12 hour done)"


def cache_missing_user_message(error: BaseException) -> str | None:
    if not isinstance(error, RuntimeError):
        return None
    text = str(error)
    if not text.startswith("MODEL_CACHE_MISSING"):
        return None
    return text


def ensure_cache(preferred_profile: str, allow_failover: bool) -> tuple[dict, str]:
    return call_inference("ensure_model_cached", preferred_profile=preferred_profile, allow_failover=allow_failover)


def embedding_status(preferred_profile: str, allow_failover: bool) -> tuple[dict, str]:
    return call_inference("embedding_status", preferred_profile=preferred_profile, allow_failover=allow_failover)


def ensure_cache_ready(preferred_profile: str, allow_failover: bool) -> str:
    status, used_profile = embedding_status(preferred_profile, allow_failover)
    if bool(status.get("ready")):
        return used_profile
    if AUTO_BUILD_MISSING_CACHE:
        _, used_profile = ensure_cache(used_profile, allow_failover and used_profile == profile_for_workspace("primary"))
        return used_profile
    raise RuntimeError(
        "MODEL_CACHE_MISSING embedded model is not ready; auto-build is disabled to avoid hidden GPU charges"
    )


def create_session(
    title: str,
    preferred_profile: str,
    allow_failover: bool,
    carryover_session_id: str = "",
    carryover_max_turns: int = 6,
) -> tuple[str, str]:
    metadata_json = json.dumps(
        {
            "carryover_session_id": str(carryover_session_id or "").strip(),
            "carryover_max_turns": int(carryover_max_turns),
        }
    )
    payload, used_profile = call_inference(
        "create_session",
        title,
        metadata_json,
        preferred_profile=preferred_profile,
        allow_failover=allow_failover,
    )
    return payload["session_id"], used_profile


def session_title() -> str:
    return f"{TITLE}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _empty_profile_session_state() -> dict[str, Any]:
    return {"current": {}, "history": []}


def _empty_session_state() -> dict[str, Any]:
    return {"profiles": {}}


def _normalize_history_entry(entry: dict[str, Any]) -> dict[str, str]:
    return {
        "session_id": str(entry.get("session_id") or "").strip(),
        "profile": normalize_session_profile(str(entry.get("profile") or DEFAULT_PROFILE)),
        "title": str(entry.get("title") or "").strip(),
        "created_at": str(entry.get("created_at") or "").strip(),
        "last_used_at": str(entry.get("last_used_at") or "").strip(),
        "carryover_source_session_id": str(entry.get("carryover_source_session_id") or "").strip(),
    }


def _load_session_state() -> dict[str, Any]:
    if not SESSION_FILE.exists():
        return _empty_session_state()
    try:
        data = json.loads(SESSION_FILE.read_text())
    except Exception:
        return _empty_session_state()

    if isinstance(data, dict) and "profiles" in data and isinstance(data["profiles"], dict):
        state = _empty_session_state()
        for profile_name, bucket in data["profiles"].items():
            normalized_profile = normalize_session_profile(profile_name)
            current = {}
            history = []
            if isinstance(bucket, dict):
                raw_current = bucket.get("current")
                if isinstance(raw_current, dict):
                    current = _normalize_history_entry(raw_current)
                raw_history = bucket.get("history")
                if isinstance(raw_history, list):
                    history = [
                        normalized
                        for item in raw_history
                        if isinstance(item, dict)
                        and (normalized := _normalize_history_entry(item)).get("session_id")
                    ]
            state["profiles"][normalized_profile] = {"current": current, "history": history}
        return state

    # Backward compatibility with older single-session schema.
    if isinstance(data, dict):
        session_id = str(data.get("session_id") or "").strip()
        profile = normalize_session_profile(str(data.get("profile") or DEFAULT_PROFILE))
        if session_id:
            entry = _normalize_history_entry(
                {
                    "session_id": session_id,
                    "profile": profile,
                    "title": str(data.get("title") or "").strip(),
                    "created_at": str(data.get("created_at") or "").strip(),
                    "last_used_at": str(data.get("last_used_at") or "").strip(),
                }
            )
            return {"profiles": {profile: {"current": entry, "history": [entry]}}}
    return _empty_session_state()


def _save_session_state(state: dict[str, Any]) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps(state, indent=2))


def _profile_bucket(state: dict[str, Any], profile: str) -> dict[str, Any]:
    normalized_profile = normalize_session_profile(profile)
    profiles = state.setdefault("profiles", {})
    bucket = profiles.get(normalized_profile)
    if not isinstance(bucket, dict):
        bucket = _empty_profile_session_state()
        profiles[normalized_profile] = bucket
    bucket.setdefault("current", {})
    bucket.setdefault("history", [])
    return bucket


def load_session(profile: str | None = None) -> tuple[str, str]:
    normalized_profile = normalize_session_profile(profile or DEFAULT_PROFILE)
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    current = bucket.get("current")
    if not isinstance(current, dict):
        return "", normalized_profile
    session_id = str(current.get("session_id") or "").strip()
    return session_id, normalized_profile


def list_sessions(profile: str | None = None) -> list[dict[str, str]]:
    normalized_profile = normalize_session_profile(profile or DEFAULT_PROFILE)
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    raw_history = bucket.get("history")
    if not isinstance(raw_history, list):
        return []
    return [
        normalized
        for item in raw_history
        if isinstance(item, dict) and (normalized := _normalize_history_entry(item)).get("session_id")
    ]


def current_session_entry(profile: str | None = None) -> dict[str, str]:
    normalized_profile = normalize_session_profile(profile or DEFAULT_PROFILE)
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    current = bucket.get("current")
    if isinstance(current, dict):
        return _normalize_history_entry(current)
    return _normalize_history_entry({})


def save_session(
    session_id: str,
    profile: str,
    *,
    title: str = "",
    created_at: str = "",
    carryover_source_session_id: str = "",
) -> None:
    normalized_profile = normalize_session_profile(profile)
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    existing_history = list_sessions(normalized_profile)
    existing = next((entry for entry in existing_history if entry.get("session_id") == session_id), {})
    timestamp = now_local_iso()
    entry = _normalize_history_entry(
        {
            "session_id": session_id,
            "profile": normalized_profile,
            "title": title or existing.get("title") or "",
            "created_at": created_at or existing.get("created_at") or timestamp,
            "last_used_at": timestamp,
            "carryover_source_session_id": carryover_source_session_id or existing.get("carryover_source_session_id") or "",
        }
    )
    history = [entry] + [item for item in existing_history if item.get("session_id") != session_id]
    bucket["current"] = entry
    bucket["history"] = history[:MAX_SESSION_HISTORY]
    _save_session_state(state)


def rename_session(
    session_id: str,
    profile: str,
    new_title: str,
) -> dict[str, str]:
    normalized_profile = normalize_session_profile(profile)
    title = str(new_title or "").strip()
    if not title:
        raise ValueError("Usage: /rename <title>")
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    history = list_sessions(normalized_profile)
    updated_entry: dict[str, str] | None = None
    updated_history = []
    for entry in history:
        if entry.get("session_id") == session_id:
            refreshed = dict(entry)
            refreshed["title"] = title
            refreshed["last_used_at"] = now_local_iso()
            updated_entry = _normalize_history_entry(refreshed)
            updated_history.append(updated_entry)
        else:
            updated_history.append(entry)
    if updated_entry is None:
        updated_entry = _normalize_history_entry(
            {
                "session_id": session_id,
                "profile": normalized_profile,
                "title": title,
                "created_at": now_local_iso(),
                "last_used_at": now_local_iso(),
            }
        )
        updated_history.insert(0, updated_entry)
    bucket["history"] = updated_history[:MAX_SESSION_HISTORY]
    current = bucket.get("current")
    if isinstance(current, dict) and str(current.get("session_id") or "") == session_id:
        bucket["current"] = updated_entry
    _save_session_state(state)
    return updated_entry


def delete_session(selection: str, profile: str) -> tuple[dict[str, str], bool]:
    normalized_profile = normalize_session_profile(profile)
    history = list_sessions(normalized_profile)
    token = str(selection or "").strip()
    if not token:
        raise ValueError("Usage: /delete <number|prefix>")
    target: dict[str, str] | None = None
    if token.isdigit():
        index = int(token)
        if index <= 0 or index > len(history):
            raise ValueError(f"No chat #{index} in history.")
        target = history[index - 1]
    else:
        for entry in history:
            if str(entry.get("session_id") or "").startswith(token):
                target = entry
                break
    if target is None:
        raise ValueError(f"No chat matching '{token}'.")

    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    remaining = [entry for entry in history if entry.get("session_id") != target.get("session_id")]
    current = current_session_entry(normalized_profile)
    deleted_current = current.get("session_id") == target.get("session_id")
    bucket["history"] = remaining[:MAX_SESSION_HISTORY]
    if deleted_current:
        bucket["current"] = remaining[0] if remaining else {}
    _save_session_state(state)
    return target, deleted_current


def clear_session(profile: str | None = None) -> None:
    normalized_profile = normalize_session_profile(profile or DEFAULT_PROFILE)
    state = _load_session_state()
    bucket = _profile_bucket(state, normalized_profile)
    bucket["current"] = {}
    _save_session_state(state)


def _modal_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env["MODAL_CONFIG_PATH"] = MODAL_CONFIG_PATH
    return env


def _activate_modal_profile(profile: str) -> None:
    subprocess.run(
        [MODAL_BIN, "profile", "activate", profile],
        capture_output=True,
        text=True,
        env=_modal_cli_env(),
        timeout=20,
        check=False,
    )


def _run_modal_json(*args: str, profile: str) -> list[dict]:
    try:
        _activate_modal_profile(profile)
        proc = subprocess.run(
            [MODAL_BIN, *args, "--json"],
            capture_output=True,
            text=True,
            env=_modal_cli_env(),
            timeout=20,
            check=True,
        )
    except Exception:
        return []
    try:
        return json.loads(proc.stdout or "[]")
    except Exception:
        return []


def _entry_matches_inference_app(entry: dict) -> bool:
    return _entry_matches_any_app(entry, (INFERENCE_APP_NAME,))


def _entry_matches_chargeable_project_app(entry: dict) -> bool:
    return _entry_matches_any_app(entry, (INFERENCE_APP_NAME, BRIDGE_APP_NAME, WEB_SEARCH_APP_NAME))


def _entry_container_id(entry: dict) -> str:
    for key in ("Container ID", "Container Id", "container_id", "containerId", "ID", "Id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _entry_app_id(entry: dict) -> str:
    for key in ("App ID", "App Id", "app_id", "appId", "ID", "Id"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _entry_task_count(entry: dict) -> int:
    for key in ("Tasks", "tasks", "task_count", "taskCount"):
        value = entry.get(key)
        if isinstance(value, int):
            return max(0, value)
        if isinstance(value, str):
            try:
                return max(0, int(value.strip()))
            except Exception:
                continue
    return 0


def live_inference_container_ids(profile: str) -> list[str]:
    containers = _run_modal_json("container", "list", profile=profile)
    return [
        container_id
        for entry in containers
        if _entry_matches_inference_app(entry) and (container_id := _entry_container_id(entry))
    ]


def live_chargeable_container_ids(profile: str, stop_all: bool = False) -> list[str]:
    containers = _run_modal_json("container", "list", profile=profile)
    return [
        container_id
        for entry in containers
        if (container_id := _entry_container_id(entry))
        and (stop_all or _entry_matches_chargeable_project_app(entry))
    ]


def live_chargeable_app_ids(profile: str, stop_all: bool = False) -> list[str]:
    apps = _run_modal_json("app", "list", profile=profile)
    return [
        app_id
        for entry in apps
        if (app_id := _entry_app_id(entry))
        and _entry_task_count(entry) > 0
        and (stop_all or _entry_matches_chargeable_project_app(entry))
    ]


def live_chargeable_task_count(profile: str, stop_all: bool = False) -> int:
    apps = _run_modal_json("app", "list", profile=profile)
    return sum(
        _entry_task_count(entry)
        for entry in apps
        if stop_all or _entry_matches_chargeable_project_app(entry)
    )


def has_live_inference_container(profile: str) -> bool:
    return bool(live_inference_container_ids(profile))


def has_live_chargeable_container(profile: str, stop_all: bool = False) -> bool:
    return bool(live_chargeable_container_ids(profile, stop_all=stop_all))


def has_live_chargeable_work(profile: str, stop_all: bool = False) -> bool:
    return has_live_chargeable_container(profile, stop_all=stop_all) or live_chargeable_task_count(
        profile, stop_all=stop_all
    ) > 0


def touch_live_inference_container(profile: str) -> bool:
    for container_id in live_inference_container_ids(profile):
        try:
            _activate_modal_profile(profile)
            proc = subprocess.run(
                [MODAL_BIN, "container", "exec", container_id, "--", "/bin/sh", "-lc", "true"],
                capture_output=True,
                text=True,
                env=_modal_cli_env(),
                timeout=20,
                check=False,
            )
        except Exception:
            continue
        if proc.returncode == 0:
            return True
    return False


class WarmKeeper:
    def __init__(self) -> None:
        self._enabled = INTERACTIVE_WARM_KEEPALIVE_ENABLED
        self._done = threading.Event()
        self._lock = threading.Lock()
        self._profile = ""
        self._deadline = 0.0
        self._paused = True
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        if self._enabled:
            self._thread.start()

    def stop(self) -> None:
        self._done.set()
        if self._enabled:
            self._thread.join(timeout=1)

    def refresh(self, profile: str) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._profile = profile
            self._deadline = float("inf") if INTERACTIVE_WARM_HOLD_UNTIL_EXIT else (time.monotonic() + INTERACTIVE_WARM_KEEPALIVE_TTL_SECONDS)
            self._paused = False
        touch_live_inference_container(profile)

    def pause(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._paused = True

    def clear(self) -> None:
        if not self._enabled:
            return
        with self._lock:
            self._profile = ""
            self._deadline = 0.0
            self._paused = True

    def expects_warm_reuse(self, profile: str) -> bool:
        if not self._enabled:
            return False
        with self._lock:
            if self._profile != profile:
                return False
            if self._deadline == float("inf"):
                return True
            return time.monotonic() < self._deadline

    def _snapshot(self) -> tuple[str, float, bool]:
        with self._lock:
            return self._profile, self._deadline, self._paused

    def _run(self) -> None:
        interval = max(5.0, INTERACTIVE_WARM_KEEPALIVE_INTERVAL_SECONDS)
        while not self._done.wait(interval):
            profile, deadline, paused = self._snapshot()
            expired = deadline != float("inf") and time.monotonic() >= deadline
            if paused or not profile or expired:
                continue
            touch_live_inference_container(profile)


def stop_live_inference_containers(*profiles: str) -> None:
    requested = tuple(profile for profile in profiles if profile)
    profile_list = list(
        dict.fromkeys(requested or _project_profiles())
    )
    for profile in profile_list:
        containers = _run_modal_json("container", "list", profile=profile)
        for entry in containers:
            if not _entry_matches_inference_app(entry):
                continue
            container_id = _entry_container_id(entry)
            if not container_id:
                continue
            try:
                _activate_modal_profile(profile)
                subprocess.run(
                    [MODAL_BIN, "container", "stop", container_id],
                    capture_output=True,
                    text=True,
                    env=_modal_cli_env(),
                    timeout=20,
                    check=False,
                )
            except Exception:
                continue


def stop_live_chargeable_containers(*profiles: str, stop_all: bool = False) -> None:
    requested = tuple(profile for profile in profiles if profile)
    profile_list = list(dict.fromkeys(requested or _project_profiles()))
    for profile in profile_list:
        container_ids = live_chargeable_container_ids(profile, stop_all=stop_all)
        for container_id in container_ids:
            try:
                _activate_modal_profile(profile)
                subprocess.run(
                    [MODAL_BIN, "container", "stop", container_id],
                    capture_output=True,
                    text=True,
                    env=_modal_cli_env(),
                    timeout=20,
                    check=False,
                )
            except Exception:
                continue


def stop_live_chargeable_apps(*profiles: str, stop_all: bool = False) -> None:
    requested = tuple(profile for profile in profiles if profile)
    profile_list = list(dict.fromkeys(requested or _project_profiles()))
    for profile in profile_list:
        app_ids = live_chargeable_app_ids(profile, stop_all=stop_all)
        for app_id in app_ids:
            try:
                _activate_modal_profile(profile)
                subprocess.run(
                    [MODAL_BIN, "app", "stop", app_id],
                    capture_output=True,
                    text=True,
                    env=_modal_cli_env(),
                    timeout=20,
                    check=False,
                )
            except Exception:
                continue


def wait_for_inference_shutdown(*profiles: str, timeout_seconds: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    requested = tuple(profile for profile in profiles if profile)
    profile_list = list(
        dict.fromkeys(requested or _project_profiles())
    )
    while time.monotonic() < deadline:
        if not any(has_live_inference_container(profile) for profile in profile_list):
            return
        time.sleep(1)


def wait_for_chargeable_shutdown(*profiles: str, timeout_seconds: float = 20.0, stop_all: bool = False) -> None:
    deadline = time.monotonic() + timeout_seconds
    requested = tuple(profile for profile in profiles if profile)
    profile_list = list(dict.fromkeys(requested or _project_profiles()))
    while time.monotonic() < deadline:
        if not any(has_live_chargeable_work(profile, stop_all=stop_all) for profile in profile_list):
            return
        time.sleep(1)


def enforce_hard_cap_shutdown(reason: str) -> None:
    with _HARD_CAP_SHUTDOWN_LOCK:
        profiles = _project_profiles()
        stop_all = HARD_CAP_STOP_ALL_CONTAINERS
        attempts = max(1, HARD_CAP_STOP_RETRY_ATTEMPTS)
        wait_timeout = max(5.0, HARD_CAP_SHUTDOWN_TIMEOUT_SECONDS / attempts)
        if DEBUG:
            mode = "all-containers" if stop_all else "project-chargeable-only"
            print(
                f"[hard-cap] emergency shutdown triggered ({reason}); mode={mode}; profiles={profiles}; attempts={attempts}",
                file=sys.stderr,
                flush=True,
            )

        for attempt in range(1, attempts + 1):
            stop_live_chargeable_containers(*profiles, stop_all=stop_all)
            stop_live_chargeable_apps(*profiles, stop_all=stop_all)
            wait_for_chargeable_shutdown(
                *profiles,
                timeout_seconds=wait_timeout,
                stop_all=stop_all,
            )
            if not any(has_live_chargeable_work(profile, stop_all=stop_all) for profile in profiles):
                return
            if attempt < attempts:
                time.sleep(max(0.0, HARD_CAP_STOP_RETRY_SLEEP_SECONDS))

        print(
            f"WARNING: hard-cap shutdown incomplete after {attempts} attempts; "
            f"manual verification required for profiles={profiles}",
            file=sys.stderr,
            flush=True,
        )
        if HARD_CAP_FAIL_CLOSED_LOCK_ON_INCOMPLETE:
            lock_all_profiles_fail_closed(
                "auto-fail-closed-incomplete-shutdown",
                json.dumps(
                    {
                        "source": "custom_model.py",
                        "reason": reason,
                        "attempts": attempts,
                    }
                ),
            )


def run_chat_turn(
    prompt: str,
    session_id: str,
    max_tokens: int,
    temperature: float,
    history_limit: int,
    preferred_profile: str,
    allow_failover: bool,
    reasoning_mode: str,
    web_mode: str,
    reporter: StatusReporter | None = None,
    warm_expected: bool = False,
) -> tuple[str, str]:
    bundle, used_profile = call_inference(
        "prepare_inference_request",
        prompt,
        session_id,
        history_limit,
        preferred_profile=preferred_profile,
        allow_failover=allow_failover,
    )
    decision = bundle["decision"]["route"]

    if decision == "refuse":
        content = bundle["refusal_message"]
        if reporter is not None:
            reporter.set_phase("saving")
        _, store_profile = call_inference(
            "store_turn",
            session_id,
            prompt,
            content,
            preferred_profile=used_profile,
            allow_failover=False,
        )
        return content, store_profile

    if should_use_web_search(prompt, web_mode):
        if reporter is not None:
            reporter.set_phase("searching_web")
        try:
            web_payload, _search_profile = search_web(prompt, used_profile)
            bundle["messages"] = inject_web_context(list(bundle["messages"]), web_payload)
        except Exception as search_error:  # noqa: BLE001
            if DEBUG:
                print(f"[web-search] search failed: {search_error}", file=sys.stderr, flush=True)

    reasoning_enabled = should_use_reasoning(prompt, reasoning_mode)

    if reporter is not None:
        reporter.set_phase("reusing" if warm_expected else "waiting_gpu")
    generation_profile = ensure_cache_ready(
        used_profile,
        allow_failover and used_profile == preferred_profile,
    )
    generation_profile = choose_generation_profile(generation_profile, allow_failover and generation_profile == preferred_profile)
    reserve_payload = reserve_quota_for_generation(
        generation_profile,
        HARD_GPU_CAP_RESERVE_SECONDS,
        "preflight-reserve",
        json.dumps(
            {
                "source": "custom_model.py",
                "session_id": session_id,
                "prompt_chars": len(prompt),
                "history_limit": history_limit,
            }
        ),
    )
    reservation_id = str(reserve_payload.get("reservation_id") or "")
    if bool(reserve_payload.get("blocked")) or not bool(reserve_payload.get("granted")) or not reservation_id:
        workspace_label = workspace_for_profile(generation_profile)
        if bool(reserve_payload.get("locked")):
            raise RuntimeError(
                "HARD_USAGE_CAP_REACHED "
                f"workspace={workspace_label} "
                "locked=1 "
                f"lock_reason={str(reserve_payload.get('lock_reason') or '').strip()} "
                f"used_seconds={float(reserve_payload.get('effective_used_seconds', 0.0)):.3f} "
                f"cap_seconds={float(reserve_payload.get('cap_seconds', cap_seconds_for_workspace(workspace_label))):.3f}"
            )
        enforce_hard_cap_shutdown("reservation-blocked")
        raise RuntimeError(
            "HARD_USAGE_CAP_REACHED "
            f"workspace={workspace_label} "
            f"used_seconds={float(reserve_payload.get('effective_used_seconds', 0.0)):.3f} "
            f"cap_seconds={float(reserve_payload.get('cap_seconds', cap_seconds_for_workspace(workspace_label))):.3f}"
        )
    result_holder: dict[str, object] = {}
    done = threading.Event()
    request_started_at = time.monotonic()
    gpu_started = False
    gpu_started_at: float | None = None
    warm_reuse_deadline = time.monotonic() + WARM_REUSE_GRACE_SECONDS if warm_expected else 0.0

    def generate_worker() -> None:
        try:
            result, used_profile = call_inference(
                "generate_completion",
                bundle["messages"],
                max_tokens=max_tokens,
                temperature=temperature,
                workspace_label=workspace_for_profile(generation_profile),
                reasoning_mode="on" if reasoning_enabled else "off",
                preferred_profile=generation_profile,
                allow_failover=allow_failover and generation_profile == profile_for_workspace("primary"),
            )
            result_holder["result"] = result
            result_holder["profile"] = used_profile
        except Exception as exc:  # noqa: BLE001
            result_holder["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=generate_worker, daemon=True)
    thread.start()
    while not done.wait(5):
        if reporter is not None:
            if gpu_started or has_live_inference_container(generation_profile):
                gpu_started = True
                if gpu_started_at is None:
                    gpu_started_at = time.monotonic()
                reporter.set_phase("reasoning" if reasoning_enabled else "generating")
            elif warm_expected:
                reporter.set_phase("reusing")
            elif time.monotonic() < warm_reuse_deadline:
                reporter.set_phase("reusing")
            else:
                reporter.set_phase("waiting_gpu")

    total_request_seconds = max(0.0, time.monotonic() - request_started_at)
    visible_gpu_seconds = max(0.0, time.monotonic() - gpu_started_at) if gpu_started_at is not None else 0.0
    try:
        finalize_quota_reservation(
            generation_profile,
            reservation_id,
            total_request_seconds,
            "generation-finalize",
            json.dumps(
                {
                    "source": "custom_model.py",
                    "had_error": "error" in result_holder,
                    "session_id": session_id,
                    "counting_basis": "generate_completion_wall_time",
                    "total_request_seconds": total_request_seconds,
                    "visible_gpu_seconds": visible_gpu_seconds,
                }
            ),
        )
    except Exception as finalize_error:  # noqa: BLE001
        if DEBUG:
            print(
                f"[quota] finalize failed for reservation {reservation_id}: {finalize_error}",
                file=sys.stderr,
                flush=True,
            )

    if "error" in result_holder:
        error = result_holder["error"]
        if hard_cap_user_message(error) is not None:
            enforce_hard_cap_shutdown("generation-runtime-cap")
        if isinstance(error, InputCancellation):
            raise RuntimeError("Request cancelled before completion.")
        raise error  # type: ignore[misc]

    result = result_holder["result"]  # type: ignore[assignment]
    generate_profile = result_holder["profile"]  # type: ignore[assignment]

    content = result["content"]
    if reporter is not None:
        reporter.set_phase("saving")
    _, store_profile = call_inference(
        "store_turn",
        session_id,
        prompt,
        content,
        preferred_profile=generate_profile,
        allow_failover=False,
    )
    return content, store_profile


def run_chat_turn_with_status(
    prompt: str,
    session_id: str,
    max_tokens: int,
    temperature: float,
    history_limit: int,
    preferred_profile: str,
    allow_failover: bool,
    reasoning_mode: str,
    web_mode: str,
    warm_expected: bool = False,
) -> tuple[str, str]:
    reporter = StatusReporter()
    reporter.start()
    try:
        return run_chat_turn(
            prompt,
            session_id,
            max_tokens,
            temperature,
            history_limit,
            preferred_profile,
            allow_failover,
            reasoning_mode,
            web_mode,
            reporter=reporter,
            warm_expected=warm_expected,
        )
    finally:
        reporter.stop()


def ensure_session_and_cache(force_new: bool) -> tuple[str, str, str]:
    previous_entry = current_session_entry(DEFAULT_PROFILE)
    previous_session_id = previous_entry.get("session_id", "")
    active_profile = DEFAULT_PROFILE
    allow_failover = AUTO_FAILOVER_ENABLED
    carryover_session_id = "" if force_new else previous_session_id
    title = session_title()
    created_at = now_local_iso()
    session_id, active_profile = create_session(
        title,
        DEFAULT_PROFILE,
        allow_failover,
        carryover_session_id=carryover_session_id,
    )
    save_session(
        session_id,
        active_profile,
        title=title,
        created_at=created_at,
        carryover_source_session_id=carryover_session_id,
    )
    return session_id, active_profile, title


def resume_session_from_history(selection: str, profile: str) -> tuple[str, str, str]:
    history = list_sessions(profile)
    token = str(selection or "").strip()
    if not token:
        raise ValueError("Usage: /resume <number>")
    target: dict[str, str] | None = None
    if token.isdigit():
        index = int(token)
        if index <= 0 or index > len(history):
            raise ValueError(f"No chat #{index} in history.")
        target = history[index - 1]
    else:
        for entry in history:
            session_id = str(entry.get("session_id") or "")
            if session_id.startswith(token):
                target = entry
                break
    if target is None:
        raise ValueError(f"No chat matching '{token}'.")
    session_id = str(target.get("session_id") or "")
    title = str(target.get("title") or "").strip()
    save_session(
        session_id,
        profile,
        title=title,
        created_at=str(target.get("created_at") or ""),
        carryover_source_session_id=str(target.get("carryover_source_session_id") or ""),
    )
    return session_id, normalize_session_profile(profile), title


def format_history_lines(profile: str, current_session_id: str) -> list[str]:
    history = list_sessions(profile)
    if not history:
        return ["No saved chats for this workspace."]
    lines = []
    for index, entry in enumerate(history, start=1):
        marker = "*" if str(entry.get("session_id") or "") == current_session_id else " "
        lines.append(
            f"{marker} {index:>2}. {chat_label(str(entry.get('title') or ''), str(entry.get('session_id') or ''))} "
            f"[{str(entry.get('last_used_at') or entry.get('created_at') or 'unknown')}] "
            f"{str(entry.get('session_id') or '')[:8]}"
        )
    return lines


def print_help() -> None:
    print("Commands:")
    print("  /help                 Show this help")
    print("  /status               Show usage, reasoning mode, web mode, and current chat")
    print("  /reason               Show current reasoning mode")
    print("  /reason on|auto|off   Change reasoning mode")
    print("  /think on|auto|off    Alias for /reason")
    print("  /web                  Show current web mode")
    print("  /web on|auto|off      Change web search mode")
    print("  /search <query>       Run web search directly without using the model")
    print("  /new                  Start a new blank chat")
    print("  /history              List recent chats for this workspace")
    print("  /resume <n|prefix>    Resume a previous chat by number or session prefix")
    print("  /rename <title>       Rename the current chat locally")
    print("  /delete <n|prefix>    Delete a saved chat from local history")
    print("  /clear                Clear the terminal screen")
    print("  /multiline            Show multiline mode")
    print("  /multiline on|off     Toggle multiline composer")
    print("  /send                 Send buffered multiline message")
    print("  /cancel               Cancel buffered multiline message")
    print("  /exit                 Exit the chat")


def clear_screen() -> None:
    if ANSI_ENABLED:
        sys.stdout.write("\033[2J\033[H")
        sys.stdout.flush()
    else:
        print("\n" * 40)


def multiline_status_line(enabled: bool) -> str:
    return f"[multiline {'on' if enabled else 'off'}]"


def handle_reasoning_command(prompt: str, current_mode: str) -> tuple[bool, str, str]:
    parts = str(prompt or "").strip().split()
    if not parts or parts[0].lower() not in {"/reason", "/think"}:
        return False, current_mode, ""
    if len(parts) == 1:
        mode = normalize_reasoning_mode(current_mode)
        return True, mode, f"{reasoning_status_line(mode)} commands: /reason on | /reason auto | /reason off"
    requested_mode = normalize_reasoning_mode(parts[1])
    return True, requested_mode, f"{reasoning_status_line(requested_mode)}"


def handle_web_command(prompt: str, current_mode: str) -> tuple[bool, str, str]:
    parts = str(prompt or "").strip().split()
    if not parts or parts[0].lower() != "/web":
        return False, current_mode, ""
    if len(parts) == 1:
        mode = normalize_web_mode(current_mode)
        return True, mode, f"{web_status_line(mode)} commands: /web on | /web auto | /web off"
    requested_mode = normalize_web_mode(parts[1])
    return True, requested_mode, f"{web_status_line(requested_mode)}"


def handle_multiline_command(prompt: str, current_enabled: bool) -> tuple[bool, bool, str]:
    parts = str(prompt or "").strip().split()
    if not parts or parts[0].lower() != "/multiline":
        return False, current_enabled, ""
    if len(parts) == 1:
        return True, current_enabled, multiline_status_line(current_enabled)
    requested = parts[1].strip().lower()
    if requested in {"on", "true", "1"}:
        return True, True, multiline_status_line(True)
    if requested in {"off", "false", "0"}:
        return True, False, multiline_status_line(False)
    return True, current_enabled, "Usage: /multiline on|off"


def read_multiline_prompt(first_line: str = "") -> str | None:
    lines: list[str] = [first_line] if first_line else []
    print_info("Multiline mode: enter text, then /send to submit or /cancel to abort.")
    while True:
        try:
            raw = input("... ")
        except EOFError:
            print()
            return None
        except KeyboardInterrupt:
            print()
            return None
        stripped = raw.strip()
        if stripped == "/send":
            compact_lines = [line.rstrip() for line in lines]
            return "\n".join(compact_lines).strip()
        if stripped == "/cancel":
            return ""
        lines.append(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Custom model chat")
    parser.add_argument("prompt", nargs="?", help="Optional single prompt. If omitted, interactive chat starts.")
    parser.add_argument("--new", action="store_true", help="Start a new blank chat session without carry-over memory.")
    parser.add_argument("--reset", action="store_true", help="Delete the current saved session and exit.")
    parser.add_argument(
        "--unlock-cap",
        choices=("primary", "backup", "both"),
        help="Manually clear fail-closed cap lock for one or both workspaces.",
    )
    parser.add_argument("--max-tokens", type=int, default=MAX_TOKENS_DEFAULT)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--history-limit", type=int, default=HISTORY_LIMIT_DEFAULT)
    parser.add_argument(
        "--reasoning",
        choices=("off", "auto", "on"),
        default=normalize_reasoning_mode(REASONING_MODE_DEFAULT),
        help="Reasoning mode: off disables the reasoning pass, auto enables it for complex prompts, on forces it for every turn.",
    )
    parser.add_argument(
        "--web",
        choices=("off", "auto", "on"),
        default=normalize_web_mode(WEB_SEARCH_MODE_DEFAULT),
        help="Web search mode: off disables web grounding, auto enables it for current/time-sensitive prompts, on forces it for every turn.",
    )
    args = parser.parse_args()

    try:
        if args.reset:
            clear_session(DEFAULT_PROFILE)
            return
        if args.unlock_cap:
            targets = ["primary", "backup"] if args.unlock_cap == "both" else [args.unlock_cap]
            for workspace in targets:
                profile = profile_for_workspace(workspace)
                payload = unlock_workspace_quota(
                    profile,
                    "manual-cli-unlock",
                    json.dumps({"source": "custom_model.py", "workspace": workspace}),
                )
                print(
                    f"unlock {workspace}: blocked={bool(payload.get('blocked'))} "
                    f"locked={bool(payload.get('locked'))} "
                    f"used_hours={float(payload.get('used_seconds', 0.0))/3600.0:.2f}"
                )
            return

        session_id, active_profile, active_title = ensure_session_and_cache(args.new)
        allow_failover = AUTO_FAILOVER_ENABLED
        reasoning_mode = normalize_reasoning_mode(args.reasoning)
        web_mode = normalize_web_mode(args.web)
        print_chat_header(active_profile, reasoning_mode, web_mode, active_title, session_id)

        if args.prompt is not None:
            try:
                content, active_profile = run_chat_turn_with_status(
                    args.prompt,
                    session_id,
                    args.max_tokens,
                    args.temperature,
                    args.history_limit,
                    active_profile,
                    allow_failover and active_profile == profile_for_workspace("primary"),
                    reasoning_mode,
                    web_mode,
                )
            except RuntimeError as error:
                limit_message = hard_cap_user_message(error)
                if limit_message is not None:
                    enforce_hard_cap_shutdown("single-turn-cap")
                    print(limit_message)
                    print_status_footer(active_profile, reasoning_mode, web_mode, active_title, session_id)
                    return
                cache_message = cache_missing_user_message(error)
                if cache_message is not None:
                    print(cache_message)
                    return
                raise
            save_session(session_id, active_profile, title=active_title)
            print_assistant_message(content)
            print_status_footer(active_profile, reasoning_mode, web_mode, active_title, session_id)
            return

        warm_keeper = WarmKeeper()
        warm_keeper.start()
        multiline_enabled = False
        while True:
            try:
                prompt = input("You: ").strip()
            except EOFError:
                print()
                break
            except KeyboardInterrupt:
                print()
                break

            if not prompt:
                continue
            if prompt.lower() in {"exit", "quit", ":q", "/exit"}:
                break
            if prompt.lower() == "/new":
                warm_keeper.clear()
                session_id, active_profile, active_title = ensure_session_and_cache(True)
                print_chat_header(active_profile, reasoning_mode, web_mode, active_title, session_id)
                continue
            if prompt.lower() in {"/help", "/?"}:
                print_help()
                continue
            if prompt.lower() in {"/status"}:
                status_bits = [
                    usage_status_line(active_profile),
                    reasoning_status_line(reasoning_mode),
                    web_status_line(web_mode),
                    multiline_status_line(multiline_enabled),
                    f"[chat {chat_label(active_title, session_id)}]",
                ]
                print(" ".join(status_bits))
                continue
            if prompt.lower().startswith("/search"):
                query = prompt[len("/search") :].strip()
                run_direct_web_search(query, active_profile)
                continue
            if prompt.lower() in {"/history", "/chats"}:
                print(style("Recent chats:", COLOR_MUTED))
                for line in format_history_lines(active_profile, session_id):
                    print(f"  {line}")
                continue
            if prompt.lower().startswith("/resume"):
                selection = prompt[len("/resume") :].strip()
                try:
                    session_id, active_profile, active_title = resume_session_from_history(selection, active_profile)
                except ValueError as error:
                    print(str(error))
                    continue
                warm_keeper.clear()
                print_chat_header(active_profile, reasoning_mode, web_mode, active_title, session_id)
                continue
            if prompt.lower().startswith("/rename"):
                new_title = prompt[len("/rename") :].strip()
                try:
                    updated = rename_session(session_id, active_profile, new_title)
                except ValueError as error:
                    print_warning(str(error))
                    continue
                active_title = updated.get("title") or active_title
                print_info(f"Renamed chat to: {active_title}")
                print_status_footer(active_profile, reasoning_mode, web_mode, active_title, session_id)
                continue
            if prompt.lower().startswith("/delete"):
                selection = prompt[len("/delete") :].strip()
                try:
                    deleted_entry, deleted_current = delete_session(selection, active_profile)
                except ValueError as error:
                    print_warning(str(error))
                    continue
                print_info(f"Deleted local history entry: {chat_label(str(deleted_entry.get('title') or ''), str(deleted_entry.get('session_id') or ''))}")
                if deleted_current:
                    warm_keeper.clear()
                    session_id, active_profile, active_title = ensure_session_and_cache(True)
                    print_chat_header(active_profile, reasoning_mode, web_mode, active_title, session_id)
                continue
            if prompt.lower() == "/clear":
                clear_screen()
                print_chat_header(active_profile, reasoning_mode, web_mode, active_title, session_id)
                continue
            handled_reasoning_command, reasoning_mode, reasoning_feedback = handle_reasoning_command(prompt, reasoning_mode)
            if handled_reasoning_command:
                print(reasoning_feedback)
                continue
            handled_web_command, web_mode, web_feedback = handle_web_command(prompt, web_mode)
            if handled_web_command:
                print(web_feedback)
                continue
            handled_multiline_command, multiline_enabled, multiline_feedback = handle_multiline_command(prompt, multiline_enabled)
            if handled_multiline_command:
                print(multiline_feedback)
                continue

            if multiline_enabled:
                multiline_prompt = read_multiline_prompt(prompt)
                if multiline_prompt is None:
                    break
                if multiline_prompt == "":
                    print_info("Multiline prompt cancelled.")
                    continue
                prompt = multiline_prompt

            warm_expected = warm_keeper.expects_warm_reuse(active_profile)
            warm_keeper.pause()
            try:
                content, active_profile = run_chat_turn_with_status(
                    prompt,
                    session_id,
                    args.max_tokens,
                    args.temperature,
                    args.history_limit,
                    active_profile,
                    allow_failover and active_profile == profile_for_workspace("primary"),
                    reasoning_mode,
                    web_mode,
                    warm_expected=warm_expected,
                )
            except RuntimeError as error:
                limit_message = hard_cap_user_message(error)
                if limit_message is not None:
                    enforce_hard_cap_shutdown("interactive-turn-cap")
                    print(limit_message)
                    print_status_footer(active_profile, reasoning_mode, web_mode, active_title, session_id)
                    continue
                cache_message = cache_missing_user_message(error)
                if cache_message is not None:
                    print(cache_message)
                    continue
                raise
            save_session(session_id, active_profile, title=active_title)
            warm_keeper.refresh(active_profile)
            print_assistant_message(content)
            print_status_footer(active_profile, reasoning_mode, web_mode, active_title, session_id)
            if DEBUG:
                print(f"[workspace={workspace_for_profile(active_profile)} session_id={session_id}]", file=sys.stderr)
    finally:
        if 'warm_keeper' in locals():
            warm_keeper.stop()
        target_profile = active_profile if 'active_profile' in locals() else ""
        stop_live_chargeable_containers(target_profile, stop_all=False)
        wait_for_chargeable_shutdown(target_profile, stop_all=False)


if __name__ == "__main__":
    main()
