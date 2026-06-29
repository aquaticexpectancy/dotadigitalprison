"""Instant Telegram warden script (optional — guaranteed bubbles)."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

logger = logging.getLogger("dota_prison.telegram")

ROOT = Path(__file__).resolve().parent
BOT_TOKEN_FILE = ROOT / ".telegram_bot_token"
CHAT_ID_FILE = ROOT / ".telegram_chat_id"
LINE_DELAY_SECONDS = float(os.environ.get("TELEGRAM_WARDEN_LINE_DELAY", "0.35"))


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


def load_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    if BOT_TOKEN_FILE.is_file():
        return read_secret_file(BOT_TOKEN_FILE)
    return ""


def load_chat_id() -> str:
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if chat_id:
        return chat_id
    if CHAT_ID_FILE.is_file():
        return read_secret_file(CHAT_ID_FILE)
    return ""


def hero_label(hero: object) -> str:
    if not isinstance(hero, str):
        return "meepo"
    if hero.startswith("npc_dota_hero_"):
        return hero.removeprefix("npc_dota_hero_").replace("_", " ")
    return hero


def warden_lines(hero: object, match_id: object) -> list[str]:
    name = hero_label(hero)
    return [
        "one sec checking the prison logs",
        f"bro actually picked {name} for the camera",
        "legend 1 micro is not saving you from this",
        "explain yourself before i execute code green and vaporize the client",
    ]


def send_telegram_message(bot_token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {detail[:500]}"
    except urllib.error.URLError as exc:
        return False, f"Request failed: {exc.reason}"

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return False, f"Non-JSON response: {raw[:200]}"

    if result.get("ok"):
        return True, "sent"
    return False, str(result)


def _send_warden_thread(lines: list[str], bot_token: str, chat_id: str) -> None:
    for line in lines:
        ok, detail = send_telegram_message(bot_token, chat_id, line)
        if ok:
            logger.info("Telegram warden: %s", line)
        else:
            logger.error("Telegram warden failed: %s", detail)
            return
        time.sleep(LINE_DELAY_SECONDS)


def notify_cheese_warden_telegram(alert: dict[str, Any]) -> bool:
    """Send 4 warden lines instantly via Telegram Bot API. Returns True if configured and sent."""
    bot_token = load_bot_token()
    chat_id = load_chat_id()
    if not bot_token or not chat_id:
        logger.debug("Telegram not configured — skip instant warden script")
        return False

    details = alert.get("details") or {}
    lines = warden_lines(details.get("hero"), details.get("match_id", "unknown"))
    threading.Thread(
        target=_send_warden_thread,
        args=(lines, bot_token, chat_id),
        daemon=True,
    ).start()
    return True


def is_configured() -> bool:
    return bool(load_bot_token() and load_chat_id())
