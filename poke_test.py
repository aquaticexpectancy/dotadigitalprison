"""Test Poke api-message for CODE GREEN."""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path

from poke_notify import (
    build_code_green_message,
    issue_execute_token,
    load_poke_api_key,
    load_poke_webhook,
    notify_code_green_sync,
    send_to_poke,
)

FORBIDDEN_HEROES = (
    "npc_dota_hero_meepo",
    "npc_dota_hero_huskar",
    "npc_dota_hero_broodmother",
)


def fake_match_id() -> str:
    return str(secrets.randbelow(900_000_000) + 8_100_000_000)


def fake_cheese_alert(*, dry_run: bool = False) -> dict:
    details = {
        "match_id": fake_match_id(),
        "hero": secrets.choice(FORBIDDEN_HEROES),
    }
    if dry_run:
        details["dry_run"] = True
        details["gamemode"] = "poke_execute_flow_test"
    return {
        "jailed_reason": "cheese_pick",
        "summary": "Forbidden hero draft",
        "details": details,
    }


def main() -> None:
    if not load_poke_api_key():
        print("No .poke_api_key")
        return

    hook = load_poke_webhook()
    print(f"Poke key: ...{load_poke_api_key()[-4:]}")
    print(f"Webhook: {'configured' if hook else 'MISSING'}")
    print(f"Default notify: POKE_NOTIFY_MODE from env (default webhook)")
    print()

    while True:
        print("  1. api-message ping")
        print("  2. cheese CODE GREEN (single api-message via notify_code_green)")
        print("  3. dry_run test alert (no taskkill)")
        print("  4. API -> MCP handshake test")
        print("  5. v2 demo receipts (skip_ack dry_run)")
        print("  q. quit")
        choice = input("> ").strip().lower()
        if choice in {"q", "quit"}:
            break
        if choice == "1":
            msg = "poke — reply hi on telegram if you got this"
            ok, detail, resp = send_to_poke(msg)
            print(json.dumps(resp, indent=2) if resp else detail)
            continue
        if choice == "4":
            root = Path(__file__).resolve().parent
            import subprocess

            subprocess.run([sys.executable, str(root / "poke_handshake_test.py")], cwd=str(root))
            continue
        if choice == "5":
            root = Path(__file__).resolve().parent
            import subprocess

            subprocess.run([sys.executable, str(root / "poke_v2_demo.py")], cwd=str(root))
            continue
        if choice == "3":
            alert = fake_cheese_alert(dry_run=True)
            token = issue_execute_token()
            print(build_code_green_message(alert, token))
            print(notify_code_green_sync(alert))
            continue
        if choice != "2":
            continue

        alert = fake_cheese_alert()
        token = issue_execute_token()
        print(build_code_green_message(alert, token))
        print(notify_code_green_sync(alert))
        print("Sent — check Telegram + MCP.")


if __name__ == "__main__":
    main()
