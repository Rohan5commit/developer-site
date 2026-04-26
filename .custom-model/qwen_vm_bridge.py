#!/usr/bin/env python3
"""Atomic budget/quota bridge for Modal inference gating.

This app owns the hard-cap ledger used by custom_model.py.
It supports:
- gpu_quota_status
- gpu_quota_set
- gpu_quota_reserve
- gpu_quota_finalize
"""

from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import modal

APP_NAME = os.getenv("MODAL_VM_BRIDGE_APP_NAME", "qwen-vm-bridge")
LEDGER_VOLUME_NAME = os.getenv("MODAL_LEDGER_VOLUME_NAME", "qwen-budget-ledger")
LEDGER_MAX_EVENTS = int(os.getenv("MODAL_LEDGER_MAX_EVENTS", "400"))
RESERVATION_TTL_SECONDS = float(os.getenv("MODAL_LEDGER_RESERVATION_TTL_SECONDS", "3600"))
USAGE_SEED_PRIMARY_SECONDS = float(os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_PRIMARY", "0") or "0")
USAGE_SEED_BACKUP_SECONDS = float(os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_BACKUP", "0") or "0")
QUOTA_RESET_TIMEZONE = os.getenv("MODAL_GPU_QUOTA_RESET_TIMEZONE", "Asia/Singapore")

LEDGER_ROOT = Path("/bridge-ledger")
LEDGER_FILE = LEDGER_ROOT / "quota-ledger.json"
LEDGER_LOCK = LEDGER_ROOT / "quota-ledger.lock"

app = modal.App(APP_NAME)
ledger_volume = modal.Volume.from_name(LEDGER_VOLUME_NAME, create_if_missing=True)
bridge_image = modal.Image.debian_slim().env(
    {
        "MODAL_GPU_USAGE_SEED_SECONDS_PRIMARY": os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_PRIMARY", "0"),
        "MODAL_GPU_USAGE_SEED_SECONDS_BACKUP": os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_BACKUP", "0"),
        "MODAL_LEDGER_RESERVATION_TTL_SECONDS": os.getenv("MODAL_LEDGER_RESERVATION_TTL_SECONDS", "3600"),
        "MODAL_LEDGER_MAX_EVENTS": os.getenv("MODAL_LEDGER_MAX_EVENTS", "400"),
        "MODAL_GPU_QUOTA_RESET_TIMEZONE": os.getenv("MODAL_GPU_QUOTA_RESET_TIMEZONE", "Asia/Singapore"),
    }
)


def _now() -> float:
    return time.time()


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(_now()))


def _reset_timezone_name() -> str:
    timezone_name = (os.getenv("MODAL_GPU_QUOTA_RESET_TIMEZONE", QUOTA_RESET_TIMEZONE) or "").strip()
    return timezone_name or "Asia/Singapore"


def _current_period_key() -> str:
    timezone_name = _reset_timezone_name()
    try:
        tzinfo = ZoneInfo(timezone_name)
    except Exception:
        tzinfo = ZoneInfo("UTC")
        timezone_name = "UTC"
    now = datetime.now(tzinfo)
    return f"{now.year:04d}-{now.month:02d}"


def _normalize_workspace(workspace_label: str) -> str:
    normalized = (workspace_label or "").strip().lower()
    if normalized not in {"primary", "backup"}:
        raise ValueError(f"Unsupported workspace label: {workspace_label!r}")
    return normalized


def _normalize_cap_seconds(cap_seconds: float) -> float:
    value = float(cap_seconds)
    if value <= 0:
        raise ValueError("cap_seconds must be > 0")
    return value


def _normalize_non_negative_seconds(value: float) -> float:
    return max(0.0, float(value))


def _empty_workspace_state(workspace_label: str, cap_seconds: float) -> dict[str, Any]:
    return {
        "workspace": workspace_label,
        "cap_seconds": cap_seconds,
        "used_seconds": 0.0,
        "reserved_seconds": 0.0,
        "seed_initialized": False,
        "period_key": _current_period_key(),
        "locked": False,
        "lock_reason": "",
        "lock_metadata_json": "",
        "lock_updated_at": "",
        "last_reset_at": "",
        "updated_at": _now_iso(),
        "reservations": {},
        "events": [],
    }


def _read_ledger() -> dict[str, Any]:
    if not LEDGER_FILE.exists():
        return {"workspaces": {}}
    try:
        payload = json.loads(LEDGER_FILE.read_text() or "{}")
    except Exception:
        return {"workspaces": {}}
    if not isinstance(payload, dict):
        return {"workspaces": {}}
    workspaces = payload.get("workspaces")
    if not isinstance(workspaces, dict):
        payload["workspaces"] = {}
    return payload


def _write_ledger(ledger: dict[str, Any]) -> None:
    LEDGER_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=2, sort_keys=True))
    tmp.replace(LEDGER_FILE)


def _append_event(ws: dict[str, Any], event: str, details: dict[str, Any] | None = None) -> None:
    events = ws.setdefault("events", [])
    if not isinstance(events, list):
        events = []
        ws["events"] = events
    entry = {
        "at": _now_iso(),
        "event": event,
    }
    if details:
        entry["details"] = details
    events.append(entry)
    if len(events) > max(50, LEDGER_MAX_EVENTS):
        del events[: len(events) - LEDGER_MAX_EVENTS]


def _state_for_workspace(ledger: dict[str, Any], workspace_label: str, cap_seconds: float) -> dict[str, Any]:
    workspaces = ledger.setdefault("workspaces", {})
    if workspace_label not in workspaces or not isinstance(workspaces[workspace_label], dict):
        workspaces[workspace_label] = _empty_workspace_state(workspace_label, cap_seconds)
    ws = workspaces[workspace_label]
    ws["workspace"] = workspace_label
    ws["cap_seconds"] = _normalize_cap_seconds(cap_seconds)
    ws["used_seconds"] = _normalize_non_negative_seconds(ws.get("used_seconds", 0.0))
    ws["reserved_seconds"] = _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0))
    ws["seed_initialized"] = bool(ws.get("seed_initialized", False))
    ws["period_key"] = str(ws.get("period_key", ""))
    ws["locked"] = bool(ws.get("locked", False))
    ws["lock_reason"] = str(ws.get("lock_reason", ""))
    ws["lock_metadata_json"] = str(ws.get("lock_metadata_json", ""))
    ws["lock_updated_at"] = str(ws.get("lock_updated_at", ""))
    ws["last_reset_at"] = str(ws.get("last_reset_at", ""))
    reservations = ws.get("reservations")
    if not isinstance(reservations, dict):
        ws["reservations"] = {}
    ws["updated_at"] = _now_iso()
    return ws


def _seed_seconds_for_workspace(workspace_label: str) -> float:
    if workspace_label == "backup":
        return _normalize_non_negative_seconds(os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_BACKUP", str(USAGE_SEED_BACKUP_SECONDS)))
    return _normalize_non_negative_seconds(os.getenv("MODAL_GPU_USAGE_SEED_SECONDS_PRIMARY", str(USAGE_SEED_PRIMARY_SECONDS)))


def _maybe_seed_workspace(ws: dict[str, Any]) -> bool:
    if bool(ws.get("seed_initialized", False)):
        return False
    seed_seconds = _normalize_non_negative_seconds(_seed_seconds_for_workspace(str(ws.get("workspace", "primary"))))
    if seed_seconds <= 0.0:
        return False
    if _normalize_non_negative_seconds(ws.get("used_seconds", 0.0)) > 0.0:
        ws["seed_initialized"] = True
        return False
    ws["seed_initialized"] = True
    ws["used_seconds"] = seed_seconds
    _append_event(
        ws,
        "seed_used_seconds",
        {
            "seed_seconds": round(seed_seconds, 6),
        },
    )
    return True


def _maybe_rollover_workspace(ws: dict[str, Any]) -> bool:
    current_period = _current_period_key()
    previous_period = str(ws.get("period_key", "") or "")
    if not previous_period:
        ws["period_key"] = current_period
        return True
    if previous_period == current_period:
        return False
    previous_used = _normalize_non_negative_seconds(ws.get("used_seconds", 0.0))
    previous_reserved = _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0))
    previous_locked = bool(ws.get("locked", False))
    ws["period_key"] = current_period
    ws["used_seconds"] = 0.0
    ws["reserved_seconds"] = 0.0
    ws["reservations"] = {}
    ws["seed_initialized"] = True
    ws["locked"] = False
    ws["lock_reason"] = ""
    ws["lock_metadata_json"] = ""
    ws["lock_updated_at"] = ""
    ws["last_reset_at"] = _now_iso()
    _append_event(
        ws,
        "monthly_reset",
        {
            "previous_period": previous_period or None,
            "current_period": current_period,
            "timezone": _reset_timezone_name(),
            "previous_used_seconds": round(previous_used, 6),
            "previous_reserved_seconds": round(previous_reserved, 6),
            "previous_locked": previous_locked,
        },
    )
    return True


def _gc_reservations(ws: dict[str, Any]) -> None:
    ttl = max(60.0, RESERVATION_TTL_SECONDS)
    now = _now()
    reservations = ws.get("reservations", {})
    if not isinstance(reservations, dict) or not reservations:
        return
    released = 0.0
    stale_ids: list[str] = []
    for reservation_id, reservation in reservations.items():
        if not isinstance(reservation, dict):
            stale_ids.append(reservation_id)
            continue
        created_at = float(reservation.get("created_at_epoch", 0.0) or 0.0)
        seconds = _normalize_non_negative_seconds(reservation.get("seconds", 0.0))
        if created_at <= 0.0 or now - created_at >= ttl:
            released += seconds
            stale_ids.append(reservation_id)
    for reservation_id in stale_ids:
        reservations.pop(reservation_id, None)
    if released > 0:
        ws["reserved_seconds"] = max(0.0, _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0)) - released)
        _append_event(
            ws,
            "gc_stale_reservations",
            {
                "released_seconds": round(released, 6),
                "count": len(stale_ids),
            },
        )


def _status_payload(ws: dict[str, Any]) -> dict[str, Any]:
    cap = _normalize_cap_seconds(ws.get("cap_seconds", 1.0))
    used = _normalize_non_negative_seconds(ws.get("used_seconds", 0.0))
    reserved = _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0))
    effective = used + reserved
    remaining = max(0.0, cap - effective)
    locked = bool(ws.get("locked", False))
    blocked = locked or effective >= cap
    return {
        "workspace": ws.get("workspace", "primary"),
        "cap_seconds": cap,
        "used_seconds": used,
        "reserved_seconds": reserved,
        "effective_used_seconds": effective,
        "remaining_seconds": remaining,
        "blocked": blocked,
        "period_key": str(ws.get("period_key", "")),
        "last_reset_at": str(ws.get("last_reset_at", "")),
        "reset_timezone": _reset_timezone_name(),
        "locked": locked,
        "lock_reason": str(ws.get("lock_reason", "")),
        "lock_updated_at": str(ws.get("lock_updated_at", "")),
        "updated_at": ws.get("updated_at", _now_iso()),
    }


def _with_locked_workspace(
    workspace_label: str,
    cap_seconds: float,
    mutator,
    *,
    write_back: bool,
) -> dict[str, Any]:
    ledger_volume.reload()
    LEDGER_ROOT.mkdir(parents=True, exist_ok=True)
    LEDGER_LOCK.touch(exist_ok=True)
    with LEDGER_LOCK.open("r+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            ledger = _read_ledger()
            ws = _state_for_workspace(ledger, workspace_label, cap_seconds)
            rolled_over = _maybe_rollover_workspace(ws)
            _gc_reservations(ws)
            persist_needed = rolled_over or _maybe_seed_workspace(ws)
            mutator(ws)
            ws["updated_at"] = _now_iso()
            payload = _status_payload(ws)
            if write_back or persist_needed:
                _write_ledger(ledger)
                ledger_volume.commit()
            return payload
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_status(workspace_label: str, cap_seconds: float) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)
    return _with_locked_workspace(workspace, cap, lambda _ws: None, write_back=False)


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_set(
    workspace_label: str,
    used_seconds: float,
    cap_seconds: float,
    reason: str = "manual-set",
    metadata_json: str = "{}",
) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)
    used = _normalize_non_negative_seconds(used_seconds)

    def mutate(ws: dict[str, Any]) -> None:
        ws["used_seconds"] = min(cap, used)
        _append_event(
            ws,
            "set_used_seconds",
            {
                "reason": reason,
                "metadata_json": metadata_json,
                "used_seconds": round(ws["used_seconds"], 6),
            },
        )

    return _with_locked_workspace(workspace, cap, mutate, write_back=True)


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_reserve(
    workspace_label: str,
    cap_seconds: float,
    reserve_seconds: float,
    reason: str = "reserve",
    metadata_json: str = "{}",
) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)
    reserve = _normalize_non_negative_seconds(reserve_seconds)
    if reserve <= 0:
        raise ValueError("reserve_seconds must be > 0")

    extra: dict[str, Any] = {}

    def mutate(ws: dict[str, Any]) -> None:
        nonlocal extra
        status = _status_payload(ws)
        if bool(status.get("locked")):
            _append_event(
                ws,
                "reserve_denied_locked",
                {
                    "reason": reason,
                    "metadata_json": metadata_json,
                    "requested_seconds": round(reserve, 6),
                    "lock_reason": str(status.get("lock_reason", "")),
                },
            )
            extra = {"granted": False, "reservation_id": None}
            return
        if status["effective_used_seconds"] + reserve > cap:
            _append_event(
                ws,
                "reserve_denied",
                {
                    "reason": reason,
                    "metadata_json": metadata_json,
                    "requested_seconds": round(reserve, 6),
                },
            )
            extra = {"granted": False, "reservation_id": None}
            return

        reservation_id = f"rsv-{uuid.uuid4().hex}"
        reservations = ws.setdefault("reservations", {})
        reservations[reservation_id] = {
            "seconds": reserve,
            "created_at": _now_iso(),
            "created_at_epoch": _now(),
            "reason": reason,
            "metadata_json": metadata_json,
        }
        ws["reserved_seconds"] = _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0)) + reserve
        _append_event(
            ws,
            "reserve_granted",
            {
                "reason": reason,
                "metadata_json": metadata_json,
                "reservation_id": reservation_id,
                "reserved_seconds": round(reserve, 6),
            },
        )
        extra = {"granted": True, "reservation_id": reservation_id}

    payload = _with_locked_workspace(workspace, cap, mutate, write_back=True)
    payload.update(extra)
    if not payload.get("granted"):
        payload["blocked"] = True
    return payload


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_finalize(
    workspace_label: str,
    cap_seconds: float,
    reservation_id: str,
    actual_seconds: float,
    reason: str = "finalize",
    metadata_json: str = "{}",
) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)
    actual = _normalize_non_negative_seconds(actual_seconds)
    reservation_id = str(reservation_id or "").strip()
    extra: dict[str, Any] = {}

    def mutate(ws: dict[str, Any]) -> None:
        nonlocal extra
        reservations = ws.setdefault("reservations", {})
        released_seconds = 0.0
        found = False
        if reservation_id and reservation_id in reservations:
            reservation = reservations.pop(reservation_id)
            if isinstance(reservation, dict):
                released_seconds = _normalize_non_negative_seconds(reservation.get("seconds", 0.0))
            found = True

        ws["reserved_seconds"] = max(
            0.0,
            _normalize_non_negative_seconds(ws.get("reserved_seconds", 0.0)) - released_seconds,
        )
        ws["used_seconds"] = min(
            cap,
            _normalize_non_negative_seconds(ws.get("used_seconds", 0.0)) + actual,
        )

        _append_event(
            ws,
            "reserve_finalized",
            {
                "reason": reason,
                "metadata_json": metadata_json,
                "reservation_id": reservation_id or None,
                "reservation_found": found,
                "released_seconds": round(released_seconds, 6),
                "actual_seconds": round(actual, 6),
            },
        )
        extra = {
            "reservation_found": found,
            "released_seconds": released_seconds,
            "actual_seconds": actual,
        }

    payload = _with_locked_workspace(workspace, cap, mutate, write_back=True)
    payload.update(extra)
    return payload


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_lock(
    workspace_label: str,
    cap_seconds: float,
    reason: str = "manual-lock",
    metadata_json: str = "{}",
) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)

    def mutate(ws: dict[str, Any]) -> None:
        ws["locked"] = True
        ws["lock_reason"] = str(reason)
        ws["lock_metadata_json"] = str(metadata_json)
        ws["lock_updated_at"] = _now_iso()
        _append_event(
            ws,
            "locked",
            {
                "reason": reason,
                "metadata_json": metadata_json,
            },
        )

    return _with_locked_workspace(workspace, cap, mutate, write_back=True)


@app.function(
    image=bridge_image,
    volumes={str(LEDGER_ROOT): ledger_volume},
    timeout=120,
    max_containers=1,
)
def gpu_quota_unlock(
    workspace_label: str,
    cap_seconds: float,
    reason: str = "manual-unlock",
    metadata_json: str = "{}",
) -> dict[str, Any]:
    workspace = _normalize_workspace(workspace_label)
    cap = _normalize_cap_seconds(cap_seconds)

    def mutate(ws: dict[str, Any]) -> None:
        ws["locked"] = False
        ws["lock_reason"] = ""
        ws["lock_metadata_json"] = ""
        ws["lock_updated_at"] = _now_iso()
        _append_event(
            ws,
            "unlocked",
            {
                "reason": reason,
                "metadata_json": metadata_json,
            },
        )

    return _with_locked_workspace(workspace, cap, mutate, write_back=True)


@app.local_entrypoint()
def main(action: str = "status", workspace: str = "primary", cap_seconds: float = 43200.0) -> None:
    if action == "status":
        print(json.dumps(gpu_quota_status.remote(workspace, cap_seconds), indent=2))
        return
    if action == "reserve":
        print(json.dumps(gpu_quota_reserve.remote(workspace, cap_seconds, 660.0, "cli-test", "{}"), indent=2))
        return
    if action == "set":
        print(json.dumps(gpu_quota_set.remote(workspace, 0.0, cap_seconds, "cli-reset", "{}"), indent=2))
        return
    if action == "lock":
        print(json.dumps(gpu_quota_lock.remote(workspace, cap_seconds, "cli-lock", "{}"), indent=2))
        return
    if action == "unlock":
        print(json.dumps(gpu_quota_unlock.remote(workspace, cap_seconds, "cli-unlock", "{}"), indent=2))
        return
    raise SystemExit(f"Unsupported action: {action}")
