"""Push CODE GREEN to Poke — async httpx webhook, v2 zero-handshake default."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from pathlib import Path
from typing import Any

from poke_ack import set_pending
from poke_http import send_api_message_async, send_webhook_async
from poke_instructions import (
    build_notify_message,
    hero_label,
    is_test_alert,
    skip_ack_enabled,
)

logger = logging.getLogger("dota_prison.poke")

ROOT = Path(__file__).resolve().parent
POKE_KEY_FILE = ROOT / ".poke_api_key"
POKE_WEBHOOK_FILE = ROOT / ".poke_webhook.json"

POKE_NOTIFY_MODE = os.environ.get("POKE_NOTIFY_MODE", "both").strip().lower()


def read_secret_file(path: Path) -> str:
    raw = path.read_bytes()
    if not raw:
        return ""
    if raw.startswith(b"\xff\xfe"):
        text = raw.decode("utf-16-le")
    elif raw.startswith(b"\xfe\xff"):
        text = raw.decode("utf-16-be")
    else:
        text = raw.decode("utf-8-sig")
    return text.strip()


def load_poke_api_key() -> str:
    token = os.environ.get("POKE_API_KEY", "").strip()
    if token:
        return token
    if POKE_KEY_FILE.is_file():
        return read_secret_file(POKE_KEY_FILE)
    return ""


def load_poke_webhook() -> dict[str, str] | None:
    if not POKE_WEBHOOK_FILE.is_file():
        return None
    try:
        data = json.loads(POKE_WEBHOOK_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    url = str(data.get("webhookUrl", "")).strip()
    token = str(data.get("webhookToken", "")).strip()
    if url and token:
        return {"webhookUrl": url, "webhookToken": token}
    return None


def issue_execute_token() -> str:
    token = secrets.token_hex(4)
    set_pending(token, ttl_seconds=300)
    return token


def build_code_green_message(
    alert: dict[str, Any],
    execute_token: str,
    *,
    skip_ack: bool | None = None,
) -> str:
    return build_notify_message(alert, execute_token, skip_ack=skip_ack)


def build_webhook_payload(
    alert: dict[str, Any],
    execute_token: str,
    *,
    skip_ack: bool | None = None,
    local_killed: bool = False,
) -> dict[str, Any]:
    details = alert.get("details") or {}
    hero = details.get("hero")
    use_skip_ack = skip_ack_enabled(skip_ack)
    return {
        "event": "code_green",
        "jailed_reason": alert.get("jailed_reason") or alert.get("event"),
        "match_id": details.get("match_id"),
        "hero": hero,
        "hero_label": hero_label(hero),
        "summary": alert.get("summary"),
        "execute_token": execute_token if not use_skip_ack else "",
        "skip_ack": use_skip_ack,
        "local_killed": local_killed,
        "dry_run": is_test_alert(alert),
        "message": build_notify_message(
            alert,
            execute_token,
            skip_ack=skip_ack,
            local_killed=local_killed,
        ),
    }


def send_to_poke(message: str) -> tuple[bool, str, dict[str, Any] | None]:
    """Sync api-message — tests and setup scripts only."""
    api_key = load_poke_api_key()
    if not api_key:
        return False, "No Poke API key", None
    return asyncio.run(send_api_message_async(api_key, message))


async def send_webhook(
    alert: dict[str, Any],
    execute_token: str,
    *,
    skip_ack: bool | None = None,
    local_killed: bool = False,
) -> tuple[bool, str]:
    hook = load_poke_webhook()
    if hook is None:
        return False, "No .poke_webhook.json — run: python setup_poke_webhook.py"
    if not load_poke_api_key():
        return False, "No Poke API key"
    return await send_webhook_async(
        hook["webhookUrl"],
        hook["webhookToken"],
        build_webhook_payload(
            alert,
            execute_token,
            skip_ack=skip_ack,
            local_killed=local_killed,
        ),
    )


async def notify_code_green_async(
    alert: dict[str, Any],
    *,
    skip_ack: bool | None = None,
    local_killed: bool = False,
) -> dict[str, Any]:
    if not load_poke_api_key():
        return {"ok": False, "error": "No Poke API key"}

    mode = POKE_NOTIFY_MODE
    use_skip_ack = skip_ack_enabled(skip_ack)
    execute_token = "" if use_skip_ack else issue_execute_token()
    message = build_notify_message(
        alert,
        execute_token,
        skip_ack=skip_ack,
        local_killed=local_killed,
    )
    result: dict[str, Any] = {
        "mode": mode,
        "skip_ack": use_skip_ack,
        "local_killed": local_killed,
        "execute_token": execute_token or None,
        "message": message,
        "webhook_ok": None,
        "api_ok": None,
    }

    if mode in {"webhook", "both"}:
        wh_ok, wh_detail = await send_webhook(
            alert,
            execute_token,
            skip_ack=skip_ack,
            local_killed=local_killed,
        )
        result["webhook_ok"] = wh_ok
        result["webhook_detail"] = wh_detail
        if wh_ok:
            logger.info("Poke webhook fired for CODE GREEN")
        else:
            logger.warning("Poke webhook failed: %s", wh_detail)

    if mode in {"api", "both"}:
        api_key = load_poke_api_key()
        ok, detail, _body = await send_api_message_async(api_key, message)
        result["api_ok"] = ok
        result["api_detail"] = detail
        if ok:
            logger.info("Poke api-message sent for CODE GREEN")
        else:
            logger.error("Poke api-message failed: %s", detail)

    if mode not in {"api", "webhook", "both"}:
        result["ok"] = False
        result["error"] = f"Invalid POKE_NOTIFY_MODE={mode!r}"
        return result

    result["ok"] = True
    return result


def notify_code_green_sync(
    alert: dict[str, Any],
    *,
    skip_ack: bool | None = None,
) -> dict[str, Any]:
    return asyncio.run(notify_code_green_async(alert, skip_ack=skip_ack))


def fire_and_forget_code_green(alert: dict[str, Any], *, local_killed: bool = False) -> None:
    """Background webhook after local taskkill — never blocks GSI handler."""
    if not load_poke_api_key():
        logger.debug("No Poke API key — skipping warden report")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(
            notify_code_green_async(alert, skip_ack=True, local_killed=local_killed)
        )
        return
    loop.create_task(
        notify_code_green_async(alert, skip_ack=True, local_killed=local_killed)
    )


def notify_code_green(alert: dict[str, Any]) -> None:
    """Schedule async notify — prefer notify_code_green_async from async contexts."""
    if not load_poke_api_key():
        logger.debug("No Poke API key — set POKE_API_KEY or create .poke_api_key")
        return
    use_skip = skip_ack_enabled(None)
    logger.info(
        "CODE GREEN notify queued | skip_ack=%s | jailed_reason=%s",
        use_skip,
        alert.get("jailed_reason") or alert.get("event"),
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(notify_code_green_async(alert, skip_ack=use_skip))
        return
    loop.create_task(notify_code_green_async(alert, skip_ack=use_skip))
