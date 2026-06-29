"""CODE GREEN alert queue — watcher raises; Poke executes or pardons via MCP."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from poke_instructions import TEST_GAMEMODE

ROOT = Path(__file__).resolve().parent
CODE_GREEN_FILE = ROOT / "code_green.json"
STRIKE_RESET_FILE = ROOT / "strike_reset.flag"
PRISON_STATE = ROOT / "prison_state.json"

logger = logging.getLogger("dota_prison.code_green")
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


STALE_CODE_GREEN_SECONDS = float(
    os.environ.get("STALE_CODE_GREEN_SECONDS", "600")
)


def clear_stale_code_green() -> bool:
    """Drop CODE GREEN from a prior run (hero demo always uses match_id=0)."""
    with _lock:
        alert = _read_alert()
        if alert is None:
            return False
        details = alert.get("details") or {}
        match_id = str(details.get("match_id", ""))
        age = time.time() - float(alert.get("timestamp", 0))
        demo_match = match_id in {"0", ""}
        if not demo_match and age <= STALE_CODE_GREEN_SECONDS:
            return False
        summary = alert.get("summary") or alert.get("event")
        if CODE_GREEN_FILE.is_file():
            CODE_GREEN_FILE.unlink(missing_ok=True)

    reason = "hero demo match_id=0" if demo_match else f"{age:.0f}s old"
    logger.warning("Cleared stale CODE GREEN (%s): %s", reason, summary)
    return True


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


def is_dry_run_alert(alert: dict[str, Any]) -> bool:
    details = alert.get("details") or {}
    if details.get("dry_run") is True:
        return True
    return details.get("gamemode") == TEST_GAMEMODE


async def _taskkill_dota_async() -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "taskkill",
        "/IM",
        "dota2.exe",
        "/F",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    rc = proc.returncode if proc.returncode is not None else -1
    return rc, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")


async def execute_code_green_async(*, dry_run: bool = False) -> tuple[bool, dict[str, Any]]:
    with _lock:
        alert = _read_alert()
        if alert is None:
            return False, {
                "action": "execute",
                "ok": False,
                "message": "No CODE GREEN alert is active.",
            }

    if dry_run or is_dry_run_alert(alert):
        clear_code_green()
        request_strike_reset()
        logger.info("execute_code_green dry_run — skipped taskkill")
        return True, {
            "action": "execute",
            "ok": True,
            "dry_run": True,
            "message": "Dry run: CODE GREEN cleared without terminating Dota.",
            "violation": alert,
        }

    rc, _stdout, stderr = await _taskkill_dota_async()
    logger.info("taskkill dota2.exe rc=%s stderr=%r", rc, stderr.strip())

    clear_code_green()
    request_strike_reset()

    if rc not in (0, 128):
        return False, {
            "action": "execute",
            "ok": False,
            "message": f"taskkill failed (rc={rc}): {stderr.strip()}",
            "taskkill_rc": rc,
            "violation": alert,
        }

    if rc == 128:
        logger.info("taskkill rc=128 — dota2.exe not running (already dead?)")

    return True, {
        "action": "execute",
        "ok": True,
        "message": "Dota 2 terminated. CODE GREEN cleared.",
        "taskkill_rc": rc,
        "violation": alert,
    }


def execute_code_green(*, dry_run: bool = False) -> tuple[bool, dict[str, Any]]:
    """Sync wrapper for tests — MCP uses execute_code_green_async."""
    return asyncio.run(execute_code_green_async(dry_run=dry_run))
