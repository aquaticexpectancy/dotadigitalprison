"""Append-only GSI trace log for debugging watcher input."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_TRACE_BYTES = 10 * 1024 * 1024

_lock = threading.Lock()
_trace_file: Path | None = None


def init_trace_file(path: Path) -> None:
    global _trace_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    _trace_file = path


def trace_path() -> Path | None:
    return _trace_file


def _rotate_if_needed() -> None:
    if _trace_file is None or not _trace_file.is_file():
        return
    if _trace_file.stat().st_size <= MAX_TRACE_BYTES:
        return

    backup = _trace_file.with_suffix(".jsonl.old")
    if backup.is_file():
        backup.unlink(missing_ok=True)
    _trace_file.replace(backup)
    _trace_file.touch()


def append_trace(record: dict[str, Any]) -> None:
    if _trace_file is None:
        return

    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    with _lock:
        _rotate_if_needed()
        with _trace_file.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def build_trace_record(
    *,
    seq: int,
    payload: dict[str, Any],
    cache: Any,
    lane_grief: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    sections = sorted(key for key in payload if key != "provider")
    hero_payload = payload.get("hero")
    player_payload = payload.get("player")
    map_payload = payload.get("map")

    hero_in_payload = isinstance(hero_payload, dict)
    player_in_payload = isinstance(player_payload, dict)
    map_in_payload = isinstance(map_payload, dict)

    hero_keys = sorted(hero_payload.keys()) if hero_in_payload else []
    player_keys = sorted(player_payload.keys()) if player_in_payload else []

    record: dict[str, Any] = {
        "ts": time.time(),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "seq": seq,
        "sections": sections,
        "note": note,
        "payload_hero_keys": hero_keys,
        "payload_player_keys": player_keys,
        "map_in_payload": map_in_payload,
        "cached": {
            "match_id": cache.match_id,
            "game_time": cache.game_time,
            "clock_time": cache.clock_time,
            "game_state": cache.game_state,
            "gamemode": cache.gamemode,
            "paused": cache.paused,
            "deaths": cache.last_deaths_seen,
            "last_hits": cache.last_hits,
            "xpm": cache.xpm,
            "gpm": cache.gpm,
            "hero_name": cache.hero_name,
            "hero_level": cache.hero_level,
            "hero_xp": cache.hero_xp,
            "hero_alive": cache.hero_alive,
            "xpos": cache.xpos,
            "ypos": cache.ypos,
            "on_lane": cache.on_lane,
        },
        "lane_grief": lane_grief or {},
    }

    if map_in_payload and isinstance(map_payload, dict):
        record["map_raw"] = {
            key: map_payload.get(key)
            for key in ("matchid", "game_time", "clock_time", "game_state", "paused")
            if key in map_payload
        }

    if hero_in_payload and isinstance(hero_payload, dict):
        record["hero_raw"] = {
            key: hero_payload.get(key)
            for key in ("name", "level", "xp", "xpos", "ypos", "alive")
            if key in hero_payload
        }

    if player_in_payload and isinstance(player_payload, dict):
        record["player_raw"] = {
            key: player_payload.get(key)
            for key in ("deaths", "last_hits", "denies", "xpm", "gpm", "kills", "assists")
            if key in player_payload
        }

    return record
