"""Shared violation event log for watcher and MCP server."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EVENTS_FILE = ROOT / "recent_events.json"
MAX_EVENTS = 50

_lock = threading.Lock()


def _read_events() -> list[dict[str, Any]]:
    try:
        raw = EVENTS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return []

    events = data.get("events")
    if not isinstance(events, list):
        return []
    return [event for event in events if isinstance(event, dict)]


def _write_events(events: list[dict[str, Any]]) -> None:
    tmp = EVENTS_FILE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"events": events[-MAX_EVENTS:]}, indent=2),
        encoding="utf-8",
    )
    tmp.replace(EVENTS_FILE)


def append_event(event: str, reason: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "timestamp": time.time(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "reason": reason,
        "details": details or {},
    }

    with _lock:
        events = _read_events()
        events.append(entry)
        _write_events(events)

    return entry


def get_recent_events(limit: int = 10) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    with _lock:
        events = _read_events()

    return events[-limit:]


def get_last_violation() -> dict[str, Any] | None:
    with _lock:
        events = _read_events()

    return events[-1] if events else None


def format_violation(entry: dict[str, Any]) -> str:
    event = entry.get("event", "unknown")
    reason = entry.get("reason", "Unknown violation")
    when = entry.get("timestamp_utc", "unknown time")
    details = entry.get("details", {})

    if event == "forbidden_hero_draft":
        hero = details.get("hero", "a forbidden hero")
        mode = details.get("gamemode")
        when_at = details.get("timer_label")
        extra = f" Mode: {mode}." if mode else ""
        if when_at:
            extra += f" At {when_at}."
        return f"{when}: Drafted forbidden hero ({hero}).{extra} {reason}"

    if event == "excessive_feeding":
        strikes = details.get("strikes")
        required = details.get("strikes_required", 5)
        reset_seconds = details.get("strike_reset_seconds", 60)
        mode = details.get("gamemode", "Unknown")
        when_at = details.get("timer_label")
        extra = ""
        if strikes is not None:
            extra = (
                f" Mode: {mode}. {strikes}/{required} feed strikes"
                f" ({reset_seconds:.0f}s idle resets count)."
            )
        if when_at:
            extra += f" Last death at {when_at}."
        return f"{when}: Excessive feeding detected.{extra} {reason}"

    if event == "lane_grief":
        flags = details.get("flags", [])
        lane_pct = details.get("lane_presence_pct")
        xpm = details.get("avg_xpm")
        lhpm = details.get("avg_lhpm")
        parts = []
        if isinstance(flags, list) and flags:
            parts.append(f"Flags: {', '.join(str(flag) for flag in flags)}.")
        if lane_pct is not None:
            parts.append(f"Lane time {lane_pct:.0%}.")
        if xpm is not None:
            parts.append(f"XP/min {xpm:.0f}.")
        if lhpm is not None:
            parts.append(f"LH/min {lhpm:.1f}.")
        return f"{when}: Lane grief check (first 10 min). {' '.join(parts)} {reason}"

    return f"{when}: {reason}"
