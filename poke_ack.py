"""Local ack flag — Poke sets this via MCP when api-message is received."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
POKE_ACK_FILE = ROOT / "poke_api_ack.json"
POKE_PENDING_FILE = ROOT / "poke_handshake_pending.json"
DEFAULT_PENDING_TTL = 120.0


def read_ack() -> dict[str, Any] | None:
    try:
        data = json.loads(POKE_ACK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def clear_ack() -> None:
    if POKE_ACK_FILE.is_file():
        POKE_ACK_FILE.unlink(missing_ok=True)


def read_pending() -> dict[str, Any] | None:
    try:
        data = json.loads(POKE_PENDING_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    expires = data.get("expires")
    if isinstance(expires, (int, float)) and time.time() > float(expires):
        clear_pending()
        return None
    return data


def set_pending(token: str, ttl_seconds: float = DEFAULT_PENDING_TTL) -> dict[str, Any]:
    now = time.time()
    payload = {
        "token": token.strip(),
        "created": now,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "expires": now + ttl_seconds,
        "expires_utc": datetime.fromtimestamp(now + ttl_seconds, tz=timezone.utc).isoformat(),
    }
    tmp = POKE_PENDING_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(POKE_PENDING_FILE)
    return payload


def clear_pending() -> None:
    if POKE_PENDING_FILE.is_file():
        POKE_PENDING_FILE.unlink(missing_ok=True)


def pending_token() -> str | None:
    pending = read_pending()
    if pending is None:
        return None
    token = pending.get("token")
    return str(token).strip() if token else None


def write_ack(
    ping_token: str,
    message: str,
    source: str = "poke",
    *,
    token_matched: bool | None = None,
) -> dict[str, Any]:
    expected = pending_token()
    supplied = ping_token.strip()
    if token_matched is None:
        token_matched = expected is not None and supplied == expected

    payload: dict[str, Any] = {
        "ok": True,
        "ping_token": supplied,
        "expected_token": expected,
        "token_matched": token_matched,
        "message": message.strip(),
        "source": source,
        "timestamp": time.time(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }
    tmp = POKE_ACK_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(POKE_ACK_FILE)
    if token_matched:
        clear_pending()
    return payload


def wait_for_ack(
    ping_token: str,
    timeout_seconds: float = 90.0,
    poll_interval: float = 0.5,
) -> dict[str, Any] | None:
    """Poll until poke_api_ack.json matches ping_token with token_matched true."""
    deadline = time.time() + timeout_seconds
    token = ping_token.strip()
    while time.time() < deadline:
        ack = read_ack()
        if (
            ack
            and str(ack.get("ping_token", "")) == token
            and ack.get("token_matched") is True
        ):
            return ack
        time.sleep(poll_interval)
    return None
