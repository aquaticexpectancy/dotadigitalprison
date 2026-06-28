"""CODE GREEN alert queue — watcher raises; Poke executes or pardons via MCP."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CODE_GREEN_FILE = ROOT / "code_green.json"
STRIKE_RESET_FILE = ROOT / "strike_reset.flag"
PRISON_STATE = ROOT / "prison_state.json"

_lock = threading.Lock()

VIOLATIONS: dict[str, dict[str, str]] = {
    "forbidden_hero_draft": {
        "jailed_reason": "cheese_pick",
        "label": "Forbidden hero draft (Meepo/Huskar/Broodmother)",
    },
    "excessive_feeding": {
        "jailed_reason": "feeding",
        "label": "Excessive feeding",
    },
    "lane_grief": {
        "jailed_reason": "lane_grief",
        "label": "Lane grief / low impact (first 10 min)",
    },
}


def is_prison_locked() -> bool:
    try:
        raw = PRISON_STATE.read_text(encoding="utf-8")
        data = json.loads(raw)
        unlocked_until = data["unlocked_until"]
        if not isinstance(unlocked_until, (int, float)):
            return True
        return time.time() > float(unlocked_until)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return True


def violation_meta(event: str, details: dict[str, Any] | None = None) -> dict[str, str]:
    details = details or {}
    base = VIOLATIONS.get(
        event,
        {"jailed_reason": event, "label": event.replace("_", " ")},
    )
    summary = base["label"]
    if event == "forbidden_hero_draft":
        hero = details.get("hero")
        if hero:
            summary = f"{summary}: {hero}"
    elif event == "excessive_feeding":
        strikes = details.get("strikes")
        if strikes is not None:
            summary = f"{summary}: {strikes} strikes"
    elif event == "lane_grief":
        flags = details.get("flags")
        if isinstance(flags, list) and flags:
            summary = f"{summary}: {', '.join(str(flag) for flag in flags)}"
    return {
        "event": event,
        "jailed_reason": base["jailed_reason"],
        "label": base["label"],
        "summary": summary,
    }


def _read_alert() -> dict[str, Any] | None:
    try:
        raw = CODE_GREEN_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict) or not data.get("active"):
        return None
    return data


def _write_alert(payload: dict[str, Any]) -> None:
    tmp = CODE_GREEN_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(CODE_GREEN_FILE)


def _same_active_alert(
    existing: dict[str, Any],
    event: str,
    details: dict[str, Any],
) -> bool:
    if existing.get("event") != event:
        return False

    old_details = existing.get("details") or {}
    old_match = old_details.get("match_id")
    new_match = details.get("match_id")
    if old_match is not None and new_match is not None:
        return str(old_match) == str(new_match)

    if event == "forbidden_hero_draft":
        return old_details.get("hero") == details.get("hero")

    return True


def raise_code_green(
    event: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Raise CODE GREEN. Returns (alert, created). created=False if already active for this violation."""
    details = details or {}
    meta = violation_meta(event, details)
    alert: dict[str, Any] = {
        "active": True,
        "code": "green",
        "timestamp": time.time(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": meta["event"],
        "jailed_reason": meta["jailed_reason"],
        "label": meta["label"],
        "summary": meta["summary"],
        "reason": reason,
        "details": details,
    }

    with _lock:
        existing = _read_alert()
        if existing is not None:
            if _same_active_alert(existing, event, details):
                return existing, False

            old_match = (existing.get("details") or {}).get("match_id")
            new_match = details.get("match_id")
            if (
                old_match is not None
                and new_match is not None
                and str(old_match) != str(new_match)
            ):
                _write_alert(alert)
                return alert, True

            return existing, False

        _write_alert(alert)
        return alert, True


def get_code_green() -> dict[str, Any] | None:
    with _lock:
        return _read_alert()


def clear_code_green() -> None:
    with _lock:
        if CODE_GREEN_FILE.is_file():
            CODE_GREEN_FILE.unlink(missing_ok=True)


def request_strike_reset() -> None:
    STRIKE_RESET_FILE.write_text("1", encoding="utf-8")


def consume_strike_reset() -> bool:
    if not STRIKE_RESET_FILE.is_file():
        return False
    STRIKE_RESET_FILE.unlink(missing_ok=True)
    return True


def prison_status() -> dict[str, Any]:
    alert = get_code_green()
    return {
        "prison_locked": is_prison_locked(),
        "code_green_active": alert is not None,
        "code_green": alert,
    }


def pardon_code_green(notes: str = "") -> tuple[bool, dict[str, Any]]:
    with _lock:
        alert = _read_alert()
        if alert is None:
            return False, {
                "action": "pardon",
                "ok": False,
                "message": "No CODE GREEN alert is active.",
            }
        clear_code_green()

    request_strike_reset()
    payload = {
        "action": "pardon",
        "ok": True,
        "message": "CODE GREEN pardoned. Dota was not terminated.",
        "notes": notes,
        "pardoned_violation": alert,
    }
    return True, payload


def execute_code_green() -> tuple[bool, dict[str, Any]]:
    with _lock:
        alert = _read_alert()
        if alert is None:
            return False, {
                "action": "execute",
                "ok": False,
                "message": "No CODE GREEN alert is active.",
            }

    result = subprocess.run(
        ["taskkill", "/IM", "dota2.exe", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )

    clear_code_green()
    request_strike_reset()

    if result.returncode != 0:
        return False, {
            "action": "execute",
            "ok": False,
            "message": f"taskkill failed (rc={result.returncode}): {result.stderr.strip()}",
            "violation": alert,
        }

    return True, {
        "action": "execute",
        "ok": True,
        "message": "Dota 2 terminated. CODE GREEN cleared.",
        "violation": alert,
    }
