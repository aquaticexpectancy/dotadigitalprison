"""End-to-end test: CODE GREEN -> Poke webhook -> MCP dry_run execute."""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
import time

from code_green import clear_code_green, get_code_green, raise_code_green
from mcp_log import MCP_LOG
from poke_ack import read_ack
from poke_instructions import TEST_GAMEMODE, skip_ack_enabled
from poke_notify import (
    POKE_NOTIFY_MODE,
    load_poke_api_key,
    load_poke_webhook,
    notify_code_green_sync,
)

POLL_SECONDS = 90
POLL_INTERVAL = 0.5
MCP_PORT = 5000


def mcp_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def fake_test_alert() -> dict:
    match_id = str(secrets.randbelow(900_000_000) + 8_100_000_000)
    _alert, _created = raise_code_green(
        "forbidden_hero_draft",
        "TEST: unauthorized cheese draft.",
        {
            "hero": "npc_dota_hero_huskar",
            "gamemode": TEST_GAMEMODE,
            "dry_run": True,
            "timer_label": "TEST",
            "match_id": match_id,
        },
    )
    return get_code_green() or {}


def mcp_dry_run_since(since: float) -> bool:
    if not MCP_LOG.is_file():
        return False
    for line in MCP_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("tool") != "execute_code_green":
            continue
        if float(row.get("ts", 0)) < since:
            continue
        args = row.get("args") or {}
        if args.get("dry_run") is True:
            return True
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="Poke CODE GREEN execute flow test (dry_run).")
    parser.add_argument(
        "--skip-ack",
        action="store_true",
        help="Set skip_ack=true in webhook payload (no poke_api_ack handshake).",
    )
    args = parser.parse_args()
    use_skip_ack = skip_ack_enabled(True if args.skip_ack else None)

    if not load_poke_api_key():
        print("Missing .poke_api_key")
        sys.exit(1)

    print("Poke execute flow test (dry_run — will NOT taskkill Dota)")
    print(f"  POKE_NOTIFY_MODE={POKE_NOTIFY_MODE}")
    print(f"  skip_ack={use_skip_ack}")

    if not mcp_port_open():
        print("  FAIL: MCP not listening on :5000 — run launch_prison.bat first")
        sys.exit(1)
    print("  MCP port :5000 OK")

    if POKE_NOTIFY_MODE in {"webhook", "both"} and load_poke_webhook() is None:
        print("  FAIL: no .poke_webhook.json — run: python setup_poke_webhook.py")
        sys.exit(1)

    print(f"  Polling up to {POLL_SECONDS}s...")
    print()

    clear_code_green()
    started = time.time()
    alert = fake_test_alert()
    sent = notify_code_green_sync(alert, skip_ack=use_skip_ack)
    print(f"  execute_token: {sent.get('execute_token')}")
    print(f"  webhook_ok: {sent.get('webhook_ok')}  api_ok: {sent.get('api_ok')}")
    if sent.get("webhook_detail"):
        print(f"  webhook: {sent['webhook_detail']}")
    if sent.get("api_detail"):
        print(f"  api: {sent['api_detail']}")
    if not sent.get("ok"):
        print(f"  notify failed: {sent.get('error')}")
        sys.exit(1)

    deadline = time.time() + POLL_SECONDS
    cleared_at: float | None = None
    mcp_at: float | None = None
    ack_at: float | None = None
    token = str(sent.get("execute_token") or "")

    while time.time() < deadline:
        if not use_skip_ack and token:
            ack = read_ack()
            if (
                ack_at is None
                and ack
                and ack.get("token_matched") is True
                and str(ack.get("ping_token")) == token
                and float(ack.get("timestamp", 0)) >= started
            ):
                ack_at = time.time()
        if mcp_at is None and mcp_dry_run_since(started):
            mcp_at = time.time()
        if get_code_green() is None:
            cleared_at = time.time()
            break
        time.sleep(POLL_INTERVAL)

    print()
    if use_skip_ack:
        print("  poke_api_ack: skipped (skip_ack=true)")
    elif ack_at is not None:
        print(f"  poke_api_ack matched (~{ack_at - started:.1f}s)")
    else:
        print("  poke_api_ack: not seen (is ngrok + MCP connected in Poke?)")

    if mcp_at is not None:
        print(f"  execute_code_green(dry_run=true) (~{mcp_at - started:.1f}s)")
    elif MCP_LOG.is_file():
        print("  execute dry_run: not in logs/mcp_tools.jsonl")
    else:
        print("  logs/mcp_tools.jsonl missing — MCP server may need restart")

    if cleared_at is not None:
        print(f"PASS (~{cleared_at - started:.1f}s), Dota untouched")
    else:
        print("FAIL: code_green still active.")
        print("  1. launch_prison.bat running")
        print("  2. python setup_poke_webhook.py")
        print("  3. re-paste config/poke_agent_brief.txt to Poke")
        print("  4. try POKE_NOTIFY_MODE=both if webhook-only fails")


if __name__ == "__main__":
    main()
