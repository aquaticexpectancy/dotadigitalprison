"""Push CODE GREEN to Poke — webhook default (automation trigger + handshake token)."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from poke import Poke

from poke_ack import set_pending
from poke_instructions import build_notify_message, hero_label, is_test_alert

logger = logging.getLogger("dota_prison.poke")

ROOT = Path(__file__).resolve().parent
POKE_KEY_FILE = ROOT / ".poke_api_key"
POKE_WEBHOOK_FILE = ROOT / ".poke_webhook.json"

# webhook = automation trigger (default, most reliable for MCP).
# api = api-message only. both = webhook + one api (worked in early tests; may duplicate agents).
POKE_NOTIFY_MODE = os.environ.get("POKE_NOTIFY_MODE", "webhook").strip().lower()


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


def build_code_green_message(alert: dict[str, Any], execute_token: str) -> str:
    return build_notify_message(alert, execute_token)


def build_webhook_payload(alert: dict[str, Any], execute_token: str) -> dict[str, Any]:
    details = alert.get("details") or {}
    hero = details.get("hero")
    return {
        "event": "code_green",
        "jailed_reason": alert.get("jailed_reason") or alert.get("event"),
        "match_id": details.get("match_id"),
        "hero": hero,
        "hero_label": hero_label(hero),
        "summary": alert.get("summary"),
        "execute_token": execute_token,
        "dry_run": is_test_alert(alert),
        "message": build_notify_message(alert, execute_token),
    }


def poke_client() -> Poke | None:
    api_key = load_poke_api_key()
    if not api_key:
        return None
    return Poke(api_key=api_key)


def send_to_poke(message: str) -> tuple[bool, str, dict[str, Any] | None]:
    client = poke_client()
    if client is None:
        return False, "No Poke API key", None
    try:
        result = client.send_message(message)
    except Exception as exc:
        return False, str(exc), None
    if result.get("success"):
        return True, result.get("message", "sent"), result
    return False, str(result), result


def send_webhook(alert: dict[str, Any], execute_token: str) -> tuple[bool, str]:
    hook = load_poke_webhook()
    client = poke_client()
    if hook is None:
        return False, "No .poke_webhook.json — run: python setup_poke_webhook.py"
    if client is None:
        return False, "No Poke API key"
    try:
        result = client.send_webhook(
            webhook_url=hook["webhookUrl"],
            webhook_token=hook["webhookToken"],
            data=build_webhook_payload(alert, execute_token),
        )
    except Exception as exc:
        return False, str(exc)
    if result.get("success"):
        return True, "webhook fired"
    return False, str(result)


def notify_code_green_sync(alert: dict[str, Any]) -> dict[str, Any]:
    """Send notify synchronously — for tests and diagnostics."""
    if not load_poke_api_key():
        return {"ok": False, "error": "No Poke API key"}

    mode = POKE_NOTIFY_MODE
    execute_token = issue_execute_token()
    message = build_notify_message(alert, execute_token)
    result: dict[str, Any] = {
        "mode": mode,
        "execute_token": execute_token,
        "message": message,
        "webhook_ok": None,
        "api_ok": None,
    }

    if mode in {"webhook", "both"}:
        wh_ok, wh_detail = send_webhook(alert, execute_token)
        result["webhook_ok"] = wh_ok
        result["webhook_detail"] = wh_detail
        if wh_ok:
            logger.info("Poke webhook fired for CODE GREEN")
        else:
            logger.warning("Poke webhook failed: %s", wh_detail)

    if mode in {"api", "both"}:
        ok, detail, _resp = send_to_poke(message)
        result["api_ok"] = ok
        result["api_detail"] = detail
        if ok:
            logger.info("Poke api-message sent: %s", message[:120])
        else:
            logger.error("Poke api-message failed: %s", detail)

    if mode not in {"api", "webhook", "both"}:
        result["ok"] = False
        result["error"] = f"Invalid POKE_NOTIFY_MODE={mode!r}"
        return result

    result["ok"] = True
    return result


def _notify_thread(alert: dict[str, Any]) -> None:
    notify_code_green_sync(alert)


def notify_code_green(alert: dict[str, Any]) -> None:
    """Notify Poke (default: webhook automation with execute_token)."""
    if not load_poke_api_key():
        logger.debug("No Poke API key — set POKE_API_KEY or create .poke_api_key")
        return
    threading.Thread(target=_notify_thread, args=(alert,), daemon=True).start()
