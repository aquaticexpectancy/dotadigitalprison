"""One-time setup: register Poke webhook for CODE GREEN → Telegram warden script."""

from __future__ import annotations

import json
from pathlib import Path

from poke import Poke

from poke_instructions import KITCHEN_BRIEF, WARDEN_ACTION
from poke_notify import POKE_WEBHOOK_FILE, load_poke_api_key

# Poke webhook action field max 500 chars
WARDEN_ACTION_MAX = 500

WARDEN_CONDITION = (
    "When the Dota Digital Prison webhook fires with event code_green"
)


def main() -> None:
    api_key = load_poke_api_key()
    if not api_key:
        print("Create .poke_api_key first (Kitchen V2 key, same account as Telegram Poke).")
        return

    client = Poke(api_key=api_key)
    action_len = len(WARDEN_ACTION)
    if action_len > WARDEN_ACTION_MAX:
        print(f"WARDEN_ACTION is {action_len} chars (max {WARDEN_ACTION_MAX}). Shorten it.")
        return

    print("Creating Poke webhook trigger...")
    print(f"  condition: {WARDEN_CONDITION}")
    print(f"  action ({action_len} chars): {WARDEN_ACTION[:80]}...")

    try:
        result = client.create_webhook(condition=WARDEN_CONDITION, action=WARDEN_ACTION)
    except Exception as exc:
        print(f"Failed: {exc}")
        return

    POKE_WEBHOOK_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print()
    print(f"Saved to {POKE_WEBHOOK_FILE}")
    print("Test: python poke_execute_flow_test.py")
    print()
    print("Re-paste brief to Poke if you changed poke_instructions.py:")
    print(f"  config/poke_agent_brief.txt (synced from poke_instructions.KITCHEN_BRIEF)")


if __name__ == "__main__":
    main()
