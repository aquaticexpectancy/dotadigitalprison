"""Single source of truth for Poke CODE GREEN instructions."""

from __future__ import annotations

from typing import Any

# Webhook trigger action (max 500 chars — enforced in setup_poke_webhook.py)
WARDEN_ACTION = (
    "CODE GREEN webhook. Read execute_token and dry_run from payload. "
    "Dota Prison MCP same turn: poke_api_ack(ping_token=execute_token), "
    "then execute_code_green(dry_run=true) if dry_run else execute_code_green(). "
    "Then Telegram roast. No status msgs. No wait."
)

KITCHEN_BRIEF = """Paste this to Poke on Telegram once (or save in Kitchen / memory).

---
Dota Digital Prison — CODE GREEN

Webhook OR api-message (not both at once — use POKE_NOTIFY_MODE=webhook default).

Every alert includes execute_token=XXXX in the payload/message.

1. poke_api_ack(ping_token=execute_token) on Dota Prison MCP
2. Then execute_code_green() — or execute_code_green(dry_run=true) for TEST/dry_run alerts
3. Then Telegram: man u messed up say gg

CHEESE: instant execute (step 2 without dry_run).
TEST / dry_run: execute_code_green(dry_run=true) only — never taskkill.
FEEDING: Telegram warn first, 60s to pardon, then execute.

Kill first, roast second. Watcher does NOT auto-kill.
---
"""

TEST_GAMEMODE = "poke_execute_flow_test"


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


def build_notify_message(alert: dict[str, Any], execute_token: str) -> str:
    """One notify payload — includes handshake token (proven to trigger MCP)."""
    jailed_reason = alert.get("jailed_reason") or alert.get("event") or "violation"
    details = alert.get("details") or {}
    match_id = details.get("match_id", "unknown")
    hero = hero_label(details.get("hero"))
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
        f"execute_code_green(). After kill, telegram: man u messed up say gg."
    )
