"""Shared fleet state and plan helpers for 3-bot coordinated mode."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
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


def get_fleet_state() -> dict[str, Any]:
    state = _read_json(fleet_state_path(), {"bots": {}})
    bots = state.setdefault("bots", {})
    for name in FLEET_BOT_NAMES:
        bot = bots.setdefault(name, {})
        bot.setdefault("enabled", True)
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

