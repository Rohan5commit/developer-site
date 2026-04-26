import os
import re
import sys
from functools import lru_cache
from typing import Any

import modal
from modal.client import Client
from modal.config import _user_config
from modal.exception import NotFoundError
from modal.exception import TimeoutError as ModalTimeoutError
from modal.exception import ResourceExhaustedError


PRIMARY_PROFILE = os.getenv("MODAL_PRIMARY_PROFILE", "secondary-failover")
BACKUP_PROFILE = os.getenv("MODAL_BACKUP_PROFILE", "default")
INFERENCE_APP_NAME = os.getenv("MODAL_INFERENCE_APP_NAME", "qwen-inference-gateway")
BRIDGE_APP_NAME = os.getenv("MODAL_VM_BRIDGE_APP_NAME", "qwen-vm-bridge")
QUIET_RUNTIME = os.getenv("CUSTOM_MODEL_DEBUG", "0") != "1"

FAILOVER_PATTERNS = (
    re.compile(r"hard_usage_cap_reached", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"budget", re.IGNORECASE),
    re.compile(r"credit", re.IGNORECASE),
    re.compile(r"billing", re.IGNORECASE),
    re.compile(r"payment required", re.IGNORECASE),
    re.compile(r"resource.?exhausted", re.IGNORECASE),
    re.compile(r"workspace limit", re.IGNORECASE),
)


def normalize_workspace(value: str) -> str:
    normalized = (value or "auto").strip().lower()
    if normalized not in {"auto", "primary", "backup"}:
        raise ValueError(f"Unsupported workspace mode: {value}")
    return normalized


def profile_for_workspace(workspace: str) -> str:
    workspace = normalize_workspace(workspace)
    if workspace == "primary":
        return PRIMARY_PROFILE
    if workspace == "backup":
        return BACKUP_PROFILE
    return PRIMARY_PROFILE


def workspace_for_profile(profile: str) -> str:
    if profile == PRIMARY_PROFILE:
        return "primary"
    if profile == BACKUP_PROFILE:
        return "backup"
    return profile


def _profile_credentials(profile: str) -> tuple[str, str]:
    if profile not in _user_config:
        raise RuntimeError(f"Modal profile {profile} is not configured locally.")
    entry = _user_config[profile]
    token_id = entry.get("token_id")
    token_secret = entry.get("token_secret")
    if not token_id or not token_secret:
        raise RuntimeError(f"Modal profile {profile} is missing credentials.")
    return str(token_id), str(token_secret)


@lru_cache(maxsize=8)
def get_client(profile: str) -> Client:
    token_id, token_secret = _profile_credentials(profile)
    return Client.from_credentials(token_id, token_secret)


def get_function(app_name: str, function_name: str, profile: str):
    return modal.Function.from_name(app_name, function_name, client=get_client(profile))


def _call_kwargs_for_profile(kwargs: dict[str, Any], profile: str) -> dict[str, Any]:
    if kwargs.get("workspace_label") not in {"primary", "backup"}:
        return kwargs
    call_kwargs = dict(kwargs)
    call_kwargs["workspace_label"] = workspace_for_profile(profile)
    return call_kwargs


def is_failover_error(exc: BaseException) -> bool:
    if isinstance(exc, NotFoundError):
        return True
    if isinstance(exc, ResourceExhaustedError):
        return True

    text_parts = [str(exc)]
    cause = getattr(exc, "__cause__", None)
    if cause is not None:
        text_parts.append(str(cause))
    context = getattr(exc, "__context__", None)
    if context is not None:
        text_parts.append(str(context))
    text = " ".join(part for part in text_parts if part).strip()
    return any(pattern.search(text) for pattern in FAILOVER_PATTERNS)


def call_modal_function(
    app_name: str,
    function_name: str,
    *args: Any,
    preferred_profile: str | None = None,
    allow_failover: bool = False,
    **kwargs: Any,
) -> tuple[Any, str]:
    attempted: list[str] = []
    if preferred_profile:
        attempted.append(preferred_profile)
    if not attempted:
        attempted.append(PRIMARY_PROFILE)
    if allow_failover and attempted[0] == PRIMARY_PROFILE and BACKUP_PROFILE not in attempted:
        attempted.append(BACKUP_PROFILE)

    last_error: BaseException | None = None
    for index, profile in enumerate(attempted):
        try:
            fn = get_function(app_name, function_name, profile)
            return fn.remote(*args, **_call_kwargs_for_profile(kwargs, profile)), profile
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            can_retry = index + 1 < len(attempted) and is_failover_error(exc)
            if can_retry:
                next_profile = attempted[index + 1]
                if not QUIET_RUNTIME:
                    print(
                        f"workspace {profile} failed on {app_name}.{function_name}; retrying {next_profile}",
                        file=sys.stderr,
                    )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to call {app_name}.{function_name}")


def spawn_modal_function(
    app_name: str,
    function_name: str,
    *args: Any,
    preferred_profile: str | None = None,
    allow_failover: bool = False,
    **kwargs: Any,
) -> tuple[modal.FunctionCall, str]:
    attempted: list[str] = []
    if preferred_profile:
        attempted.append(preferred_profile)
    if not attempted:
        attempted.append(PRIMARY_PROFILE)
    if allow_failover and attempted[0] == PRIMARY_PROFILE and BACKUP_PROFILE not in attempted:
        attempted.append(BACKUP_PROFILE)

    last_error: BaseException | None = None
    for index, profile in enumerate(attempted):
        try:
            fn = get_function(app_name, function_name, profile)
            return fn.spawn(*args, **_call_kwargs_for_profile(kwargs, profile)), profile
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            can_retry = index + 1 < len(attempted) and is_failover_error(exc)
            if can_retry:
                next_profile = attempted[index + 1]
                if not QUIET_RUNTIME:
                    print(
                        f"workspace {profile} failed on {app_name}.{function_name}; retrying {next_profile}",
                        file=sys.stderr,
                    )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Failed to spawn {app_name}.{function_name}")
