"""Shared fleet state and plan helpers for 3-bot coordinated mode."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from uuid import uuid4
from typing import Any

from config import FLEET_SHARED_DIR, FLEET_BOT_NAMES, INSTANCE_NAME, BET_TARGET

_UTC = timezone.utc


def _ensure_dir() -> None:
    os.makedirs(FLEET_SHARED_DIR, exist_ok=True)


def _path(name: str) -> str:
    _ensure_dir()
    return os.path.join(FLEET_SHARED_DIR, name)


def _atomic_write_json(path: str, data: dict[str, Any]) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=True, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _read_json(path: str, default: dict[str, Any]) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except json.JSONDecodeError:
        return default


def fleet_state_path() -> str:
    return _path("fleet_state.json")


def fleet_plan_path() -> str:
    return _path("fleet_plan.json")


def fleet_command_path() -> str:
    return _path("fleet_commands.json")


def get_fleet_state() -> dict[str, Any]:
    state = _read_json(fleet_state_path(), {"bots": {}})
    bots = state.setdefault("bots", {})
    for name in FLEET_BOT_NAMES:
        bot = bots.setdefault(name, {})
        bot.setdefault("enabled", True)
        bot.setdefault("paused", False)
    return state


def set_bot_enabled(bot_name: str, enabled: bool) -> dict[str, Any]:
    state = get_fleet_state()
    bot = state["bots"].setdefault(bot_name, {})
    bot["enabled"] = enabled
    bot["updated_at"] = datetime.now(_UTC).isoformat()
    _atomic_write_json(fleet_state_path(), state)
    return state


def is_bot_enabled(bot_name: str = INSTANCE_NAME) -> bool:
    state = get_fleet_state()
    return bool(state["bots"].get(bot_name, {}).get("enabled", True))


def set_bot_paused(bot_name: str, paused: bool) -> dict[str, Any]:
    state = get_fleet_state()
    bot = state["bots"].setdefault(bot_name, {})
    bot["paused"] = paused
    bot["updated_at"] = datetime.now(_UTC).isoformat()
    _atomic_write_json(fleet_state_path(), state)
    return state


def is_bot_paused(bot_name: str = INSTANCE_NAME) -> bool:
    state = get_fleet_state()
    return bool(state["bots"].get(bot_name, {}).get("paused", False))


def update_snapshot(snapshot: dict[str, Any], bot_name: str = INSTANCE_NAME) -> dict[str, Any]:
    state = get_fleet_state()
    bot = state["bots"].setdefault(bot_name, {})
    bot.update(snapshot)
    bot.setdefault("target", BET_TARGET)
    bot.setdefault("enabled", True)
    bot["snapshot_at"] = datetime.now(_UTC).isoformat()
    _atomic_write_json(fleet_state_path(), state)
    return state


def get_snapshots() -> dict[str, Any]:
    return get_fleet_state().get("bots", {})


def write_plan(plan: dict[str, Any]) -> None:
    payload = {
        **plan,
        "written_at": datetime.now(_UTC).isoformat(),
    }
    _atomic_write_json(fleet_plan_path(), payload)


def read_plan() -> dict[str, Any]:
    return _read_json(fleet_plan_path(), {})


def get_fleet_commands() -> dict[str, Any]:
    return _read_json(fleet_command_path(), {"commands": {}})


def enqueue_bet_now(period: str, requested_by: str = INSTANCE_NAME) -> dict[str, Any]:
    state = get_fleet_commands()
    commands = state.setdefault("commands", {})
    active = commands.get("bet_now")
    if active and active.get("period") == period and not active.get("completed_at"):
        return active

    payload = {
        "command_id": uuid4().hex,
        "action": "bet_now",
        "period": period,
        "requested_by": requested_by,
        "created_at": datetime.now(_UTC).isoformat(),
        "processed_by": {},
    }
    commands["bet_now"] = payload
    _atomic_write_json(fleet_command_path(), state)
    return payload


def get_pending_bet_now() -> dict[str, Any] | None:
    command = get_fleet_commands().get("commands", {}).get("bet_now")
    if not command or command.get("completed_at"):
        return None
    return command


def mark_bet_now_processed(
    bot_name: str,
    status: str,
    note: str = "",
    command_id: str | None = None,
) -> dict[str, Any] | None:
    state = get_fleet_commands()
    command = state.setdefault("commands", {}).get("bet_now")
    if not command:
        return None
    if command_id and command.get("command_id") != command_id:
        return command

    processed = command.setdefault("processed_by", {})
    processed[bot_name] = {
        "status": status,
        "note": note,
        "processed_at": datetime.now(_UTC).isoformat(),
    }
    if all(name in processed for name in FLEET_BOT_NAMES):
        command["completed_at"] = datetime.now(_UTC).isoformat()
    _atomic_write_json(fleet_command_path(), state)
    return command
