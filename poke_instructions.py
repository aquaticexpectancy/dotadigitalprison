"""Single source of truth for Poke CODE GREEN instructions."""

from __future__ import annotations

import os
from typing import Any

# Webhook trigger action (max 500 chars — enforced in setup_poke_webhook.py).
WARDEN_ACTION = (
    "CODE GREEN webhook. Read execute_token, dry_run, skip_ack from payload. "
    "If skip_ack: execute_code_green(dry_run=true) if dry_run else execute_code_green(). "
    "Else: poke_api_ack(ping_token=execute_token), then same execute. "
    "Telegram roast after. No status. No wait."
)

KITCHEN_BRIEF = """Paste this to Poke on Telegram once (or save in Kitchen / memory).

---
Dota Digital Prison — CODE GREEN v2 (zero-handshake)

Default: webhook + skip_ack=true → execute_code_green on MCP (no poke_api_ack).
Set POKE_REQUIRE_ACK=1 only for the slow handshake path (~18s).

CHEESE / FEEDING: Poke calls execute_code_green() (or dry_run for tests), then Telegram roast.
Watcher raises CODE GREEN and webhooks — does NOT taskkill locally.
---
"""

TEST_GAMEMODE = "poke_execute_flow_test"


def skip_ack_enabled(explicit: bool | None = None) -> bool:
    """v2 default: skip handshake unless POKE_REQUIRE_ACK=1 or explicit False."""
    if explicit is not None:
        return explicit
    require = os.environ.get("POKE_REQUIRE_ACK", "").strip().lower()
    if require in {"1", "true", "yes"}:
        return False
    legacy_off = os.environ.get("POKE_SKIP_ACK", "").strip().lower()
    if legacy_off in {"0", "false", "no"}:
        return False
    return True


def hero_label(hero: object) -> str:
    if not isinstance(hero, str):
        return "unknown hero"
    if hero.startswith("npc_dota_hero_"):
        return hero.removeprefix("npc_dota_hero_").replace("_", " ")
    return hero


def is_test_alert(alert: dict[str, Any]) -> bool:
    details = alert.get("details") or {}
    if details.get("dry_run") is True:
        return True
    return details.get("gamemode") == TEST_GAMEMODE


def build_notify_message(
    alert: dict[str, Any],
    execute_token: str,
    *,
    skip_ack: bool | None = None,
    local_killed: bool = False,
) -> str:
    """One notify payload — skip_ack default; local_killed ignored (legacy field)."""
    _ = local_killed
    jailed_reason = alert.get("jailed_reason") or alert.get("event") or "violation"
    details = alert.get("details") or {}
    match_id = details.get("match_id", "unknown")
    hero = hero_label(details.get("hero"))
    use_skip_ack = skip_ack_enabled(skip_ack)

    if use_skip_ack:
        mcp = "skip_ack=true. MCP: "
    else:
        mcp = (
            f"execute_token={execute_token}. "
            f"MCP order: poke_api_ack(ping_token={execute_token}), "
        )

    if is_test_alert(alert):
        return (
            f"CODE GREEN TEST match {match_id} hero {hero}. {mcp}"
            f"execute_code_green(dry_run=true). Do not taskkill."
        )

    if jailed_reason == "feeding":
        strikes = details.get("strikes")
        extra = f" ({strikes} strike deaths)" if strikes else ""
        return (
            f"CODE GREEN feeding{extra} match {match_id}. "
            f"Telegram warn — 60s pardon window. {mcp}"
            f"execute_code_green() if no pardon. Then roast."
        )

    return (
        f"CODE GREEN cheese {hero} match {match_id}. {mcp}"
        f"execute_code_green(). After kill, telegram roast."
    )
