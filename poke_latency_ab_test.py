"""A/B latency test: poke_api_ack handshake vs skip_ack (Poke sub-10s claim)."""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
import time
from dataclasses import dataclass

from code_green import clear_code_green, get_code_green, raise_code_green
from mcp_log import MCP_LOG
from poke_ack import clear_ack, read_ack
from poke_instructions import TEST_GAMEMODE
from poke_notify import (
    POKE_NOTIFY_MODE,
    load_poke_api_key,
    load_poke_webhook,
    notify_code_green_sync,
)

POLL_SECONDS = 90
POLL_INTERVAL = 0.5
MCP_PORT = 5000
COOLDOWN_SECONDS = 30.0


@dataclass
class TrialResult:
    label: str
    skip_ack: bool
    notify_ok: bool
    ack_s: float | None
    first_mcp_s: float | None
    first_mcp_tool: str | None
    execute_s: float | None
    cleared_s: float | None
    pass_: bool
    execute_token: str | None


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


def mcp_events_since(since: float) -> list[tuple[float, str]]:
    if not MCP_LOG.is_file():
        return []
    events: list[tuple[float, str]] = []
    for line in MCP_LOG.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = float(row.get("ts", 0))
        if ts < since:
            continue
        tool = str(row.get("tool", ""))
        if tool:
            events.append((ts, tool))
    events.sort(key=lambda item: item[0])
    return events


def mcp_execute_dry_run_since(since: float) -> float | None:
    for ts, tool in mcp_events_since(since):
        if tool != "execute_code_green":
            continue
        # Re-read row for dry_run — events list is tool-only; scan again for match
        if not MCP_LOG.is_file():
            return None
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
                return float(row.get("ts", 0))
    return None


def run_trial(label: str, *, skip_ack: bool) -> TrialResult:
    clear_code_green()
    clear_ack()
    started = time.time()
    alert = fake_test_alert()
    sent = notify_code_green_sync(alert, skip_ack=skip_ack)
    token = str(sent.get("execute_token") or "")

    if not sent.get("ok"):
        return TrialResult(
            label=label,
            skip_ack=skip_ack,
            notify_ok=False,
            ack_s=None,
            first_mcp_s=None,
            first_mcp_tool=None,
            execute_s=None,
            cleared_s=None,
            pass_=False,
            execute_token=token or None,
        )

    deadline = time.time() + POLL_SECONDS
    ack_at: float | None = None
    first_mcp_at: float | None = None
    first_mcp_tool: str | None = None
    execute_at: float | None = None
    cleared_at: float | None = None

    while time.time() < deadline:
        if not skip_ack and ack_at is None and token:
            ack = read_ack()
            if (
                ack
                and ack.get("token_matched") is True
                and str(ack.get("ping_token")) == token
                and float(ack.get("timestamp", 0)) >= started
            ):
                ack_at = time.time()

        if first_mcp_at is None:
            events = mcp_events_since(started)
            if events:
                first_ts, first_tool = events[0]
                first_mcp_at = first_ts
                first_mcp_tool = first_tool

        if execute_at is None:
            exec_ts = mcp_execute_dry_run_since(started)
            if exec_ts is not None:
                execute_at = exec_ts

        if get_code_green() is None:
            cleared_at = time.time()
            break

        time.sleep(POLL_INTERVAL)

    return TrialResult(
        label=label,
        skip_ack=skip_ack,
        notify_ok=True,
        ack_s=(ack_at - started) if ack_at else None,
        first_mcp_s=(first_mcp_at - started) if first_mcp_at else None,
        first_mcp_tool=first_mcp_tool,
        execute_s=(execute_at - started) if execute_at else None,
        cleared_s=(cleared_at - started) if cleared_at else None,
        pass_=cleared_at is not None,
        execute_token=token or None,
    )


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}s"


def print_trial(result: TrialResult) -> None:
    status = "PASS" if result.pass_ else "FAIL"
    print(f"  [{result.label}] {status}")
    print(f"    skip_ack={result.skip_ack}")
    if result.execute_token:
        print(f"    execute_token={result.execute_token}")
    print(f"    first MCP tool: {result.first_mcp_tool or 'none'} @ {fmt_seconds(result.first_mcp_s)}")
    if not result.skip_ack:
        print(f"    poke_api_ack: {fmt_seconds(result.ack_s)}")
    print(f"    execute_code_green(dry_run): {fmt_seconds(result.execute_s)}")
    print(f"    code_green cleared: {fmt_seconds(result.cleared_s)}")
    print()


def print_verdict(with_ack: TrialResult, skip_ack: TrialResult) -> None:
    print("=" * 60)
    print("VERDICT")
    print("=" * 60)

    if not with_ack.pass_:
        print("Handshake trial FAILED — fix baseline before comparing.")
        return
    if not skip_ack.pass_:
        print("Skip-ack trial FAILED — Poke's faster path does not work (yet).")
        print("Keep poke_api_ack in production.")
        return

    ack_delta = None
    if with_ack.cleared_s is not None and skip_ack.cleared_s is not None:
        ack_delta = with_ack.cleared_s - skip_ack.cleared_s

    print(f"With handshake:  cleared in {fmt_seconds(with_ack.cleared_s)}")
    print(f"Skip handshake:  cleared in {fmt_seconds(skip_ack.cleared_s)}")
    if ack_delta is not None:
        print(f"Saved by skip_ack: {ack_delta:.1f}s")

    if skip_ack.cleared_s is not None and skip_ack.cleared_s < 10.0:
        print("Poke is RIGHT: sub-10s end-to-end with skip_ack.")
    elif ack_delta is not None and ack_delta >= 3.0:
        print(
            f"Poke is PARTIALLY RIGHT: skip_ack saves ~{ack_delta:.0f}s, "
            f"but total is still {fmt_seconds(skip_ack.cleared_s)} (not sub-10s)."
        )
    elif ack_delta is not None and ack_delta > 0.5:
        print(
            f"Marginal gain ({ack_delta:.1f}s) — bottleneck is likely Poke cloud + ngrok, not ack."
        )
    else:
        print("Poke is WRONG on this run: skip_ack did not meaningfully beat handshake.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare CODE GREEN latency with vs without poke_api_ack."
    )
    parser.add_argument(
        "--skip-only",
        action="store_true",
        help="Run only the skip_ack trial (POKE_SKIP_ACK path).",
    )
    parser.add_argument(
        "--with-ack-only",
        action="store_true",
        help="Run only the handshake trial.",
    )
    args = parser.parse_args()

    if not load_poke_api_key():
        print("Missing .poke_api_key")
        sys.exit(1)

    print("Poke latency A/B (dry_run — will NOT taskkill Dota)")
    print(f"  POKE_NOTIFY_MODE={POKE_NOTIFY_MODE}")
    print("  Requires: launch_prison.bat + updated webhook (python setup_poke_webhook.py)")
    print()

    if not mcp_port_open():
        print("FAIL: MCP not listening on :5000 — run launch_prison.bat first")
        sys.exit(1)
    print("  MCP port :5000 OK")

    if POKE_NOTIFY_MODE in {"webhook", "both"} and load_poke_webhook() is None:
        print("FAIL: no .poke_webhook.json — run: python setup_poke_webhook.py")
        sys.exit(1)

    run_with = not args.skip_only
    run_skip = not args.with_ack_only

    with_ack = TrialResult(
        label="with_ack",
        skip_ack=False,
        notify_ok=False,
        ack_s=None,
        first_mcp_s=None,
        first_mcp_tool=None,
        execute_s=None,
        cleared_s=None,
        pass_=False,
        execute_token=None,
    )
    skip_ack = with_ack

    if run_with:
        print(f"Trial 1/2: WITH poke_api_ack (up to {POLL_SECONDS}s)...")
        with_ack = run_trial("with_ack", skip_ack=False)
        print_trial(with_ack)
        if run_skip:
            print(f"Cooldown {COOLDOWN_SECONDS:.0f}s before skip_ack trial...")
            time.sleep(COOLDOWN_SECONDS)

    if run_skip:
        print(f"Trial 2/2: SKIP poke_api_ack (up to {POLL_SECONDS}s)...")
        skip_ack = run_trial("skip_ack", skip_ack=True)
        print_trial(skip_ack)

    if run_with and run_skip:
        print_verdict(with_ack, skip_ack)
    elif (run_with and not with_ack.pass_) or (run_skip and not skip_ack.pass_):
        sys.exit(1)


if __name__ == "__main__":
    main()
