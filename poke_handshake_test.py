"""API → Poke → MCP ack handshake test (console proof without Telegram)."""

from __future__ import annotations

import secrets
import sys
import time

from poke_ack import clear_ack, clear_pending, read_ack, set_pending, wait_for_ack
from poke_notify import send_to_poke


def build_handshake_message(ping_token: str) -> str:
    return (
        f"poke api handshake. do not invent a token. "
        f"call get_handshake_token() on Dota Prison MCP, "
        f"then poke_api_ack(ping_token=<that exact token>, "
        f"message=\"got your api call — replying through mcp\"). "
        f"expected token is {ping_token}."
    )


def main() -> None:
    ping_token = secrets.token_hex(4)
    clear_ack()
    clear_pending()
    set_pending(ping_token, ttl_seconds=120.0)

    print("Poke API -> MCP handshake test")
    print("=" * 40)
    print(f"Ping token: {ping_token}")
    print("(also in poke_handshake_pending.json — Poke can read via get_handshake_token)")
    print("Requires: mcp_server.py + ngrok running, Poke MCP connected + tools synced")
    print()
    print("Sending api-message to Poke...")

    ok, detail, resp = send_to_poke(build_handshake_message(ping_token))
    print(f"API: ok={ok} — {detail}")
    if resp:
        print(resp)
    if not ok:
        sys.exit(1)

    print()
    print("Waiting for poke_api_ack with matching token (up to 90s)...")
    print("Watch MCP server window for: [MCP] poke_api_ack | matched=True")
    started = time.time()
    ack = wait_for_ack(ping_token, timeout_seconds=90.0)

    if ack is None:
        stale = read_ack()
        print()
        if stale and stale.get("token_matched") is False:
            print("MCP CALLED — WRONG TOKEN (Poke ignored your handshake token).")
            print(f"  Expected: {ping_token}")
            print(f"  Got:      {stale.get('ping_token')}")
            print(f"  At:       {stale.get('timestamp_utc')}")
            print()
            print("MCP works. Poke must call get_handshake_token() and copy it exactly.")
        elif stale and str(stale.get("ping_token")) != ping_token:
            print("STALE ACK on disk from an older run (wrong token).")
            print(f"  This run:  {ping_token}")
            print(f"  On disk:   {stale.get('ping_token')} @ {stale.get('timestamp_utc')}")
        else:
            print("TIMEOUT — Poke never called poke_api_ack for this run.")
            print("Check ngrok, MCP server, Poke integration, re-sync tools.")
        sys.exit(1)

    elapsed = round(time.time() - started, 1)
    print()
    print("=" * 40)
    print(f"POKE MCP ACK OK ({elapsed}s)")
    print("=" * 40)
    print(f"  token:   {ack.get('ping_token')}")
    print(f"  message: {ack.get('message')}")
    print(f"  at:      {ack.get('timestamp_utc')}")
    print()
    print("API -> Poke -> MCP confirmed for this token.")


if __name__ == "__main__":
    main()
