"""Timing run — detection to execute (skip_ack dry_run)."""

from __future__ import annotations

import asyncio
import json
import secrets
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from code_green import clear_code_green, get_code_green, raise_code_green
from mcp_log import MCP_LOG
from poke_http import close_http_client, get_http_client
from poke_instructions import TEST_GAMEMODE
from poke_notify import POKE_NOTIFY_MODE, load_poke_api_key, load_poke_webhook, notify_code_green_async

ROOT = Path(__file__).resolve().parent
RECEIPT_FILE = ROOT / "logs" / "v2_demo_receipt.json"
POLL_SECONDS = 90
POLL_INTERVAL = 0.25
MCP_PORT = 5000


def utc_ms_label(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return f"{dt.strftime('%H:%M:%S')}.{dt.microsecond // 1000:03d} utc"


def mcp_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def first_execute_since(since: float) -> float | None:
    if not MCP_LOG.is_file():
        return None
    for line in MCP_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("tool") != "execute_code_green":
            continue
        ts = float(row.get("ts", 0))
        if ts < since:
            continue
        args = row.get("args") or {}
        if args.get("dry_run") is True:
            return ts
    return None


async def run_timed_demo() -> dict[str, Any]:
    clear_code_green()
    violation_at = time.time()
    match_id = str(secrets.randbelow(900_000_000) + 8_100_000_000)
    raise_code_green(
        "forbidden_hero_draft",
        "timing run: unauthorized cheese draft.",
        {
            "hero": "npc_dota_hero_huskar",
            "gamemode": TEST_GAMEMODE,
            "dry_run": True,
            "timer_label": "TIMING",
            "match_id": match_id,
        },
    )
    alert = get_code_green() or {}

    await get_http_client()
    sent = await notify_code_green_async(alert, skip_ack=True)
    webhook_at = time.time() if sent.get("ok") else None

    execute_at: float | None = None
    cleared_at: float | None = None
    deadline = time.time() + POLL_SECONDS

    while time.time() < deadline:
        if execute_at is None:
            execute_at = first_execute_since(violation_at)
        if get_code_green() is None:
            cleared_at = time.time()
            break
        await asyncio.sleep(POLL_INTERVAL)

    local_ms = int((webhook_at - violation_at) * 1000) if webhook_at else None
    cloud_ms = (
        int((execute_at - webhook_at) * 1000)
        if execute_at is not None and webhook_at is not None
        else None
    )
    execute_ms = int((execute_at - violation_at) * 1000) if execute_at else None
    cleared_ms = int((cleared_at - violation_at) * 1000) if cleared_at else None

    return {
        "pass": cleared_at is not None and sent.get("ok"),
        "skip_ack": True,
        "poke_notify_mode": POKE_NOTIFY_MODE,
        "violation_detected": utc_ms_label(violation_at),
        "webhook_fired_local": utc_ms_label(webhook_at) if webhook_at else None,
        "execute_code_green": utc_ms_label(execute_at) if execute_at else None,
        "latency_ms": {
            "local_webhook": local_ms,
            "cloud_vaporization": cloud_ms,
            "violation_to_execute": execute_ms,
            "violation_to_cleared": cleared_ms,
        },
        "saved_vs_v1_handshake_ms": 17840 - execute_ms if execute_ms else None,
        "saved_vs_v1_execute_ms": 20000 - execute_ms if execute_ms else None,
    }


def print_receipt(receipt: dict[str, Any]) -> None:
    ms = receipt["latency_ms"]
    print(f"violation detected:     {receipt['violation_detected']}")
    print(f"webhook fired (local):  {receipt['webhook_fired_local']}  ({ms['local_webhook']}ms)")
    print(f"execute (dry_run):      {receipt['execute_code_green']}  ({ms['violation_to_execute']}ms total)")
    print(f"  cloud leg:            {ms['cloud_vaporization']}ms")
    if receipt.get("saved_vs_v1_execute_ms") is not None:
        print(f"  saved vs v1 (~20s execute): {receipt['saved_vs_v1_execute_ms']}ms")
    print(f"cleared:                {ms['violation_to_cleared']}ms")


async def main_async() -> int:
    if not load_poke_api_key():
        print("Missing .poke_api_key")
        return 1
    if not mcp_port_open():
        print("FAIL: MCP not on :5000 — restart launch_prison.bat (async mcp_server)")
        return 1
    if POKE_NOTIFY_MODE in {"webhook", "both"} and load_poke_webhook() is None:
        print("FAIL: run python setup_poke_webhook.py")
        return 1

    try:
        receipt = await run_timed_demo()
    finally:
        await close_http_client()

    print_receipt(receipt)
    RECEIPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECEIPT_FILE.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"\nSaved: {RECEIPT_FILE}")
    return 0 if receipt["pass"] else 1


def main() -> None:
    sys.exit(asyncio.run(main_async()))


if __name__ == "__main__":
    main()
