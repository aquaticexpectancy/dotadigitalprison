"""MCP server for Dota Digital Prison — state and actions for Poke."""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier

from code_green import (
    execute_code_green as run_execute_code_green,
    get_code_green as read_code_green,
    pardon_code_green as run_pardon_code_green,
    prison_status,
)
from events import format_violation, get_last_violation, get_recent_events
from poke_ack import (
    read_ack as read_poke_ack,
    read_pending as read_handshake_pending,
    write_ack as write_poke_ack,
)
from session_log import load_latest_session

from mcp_log import log_tool_call

ROOT = Path(__file__).resolve().parent
MCP_HOST = os.environ.get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(os.environ.get("MCP_PORT", "5000"))
TOKEN_FILE = ROOT / ".mcp_token"
PRISON_STATE = ROOT / "prison_state.json"


def read_token_file(path: Path) -> str:
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


def load_auth_token() -> str:
    token = os.environ.get("MCP_AUTH_TOKEN", "").strip()
    if token:
        return token

    if TOKEN_FILE.is_file():
        token = read_token_file(TOKEN_FILE)
        if token:
            return token

    return ""


def _json(data: object) -> str:
    return json.dumps(data, indent=2)


MCP_AUTH_TOKEN = load_auth_token()

if not MCP_AUTH_TOKEN:
    print(
        "Refusing to start: create .mcp_token in this folder or set MCP_AUTH_TOKEN.",
        file=sys.stderr,
    )
    sys.exit(1)

auth = StaticTokenVerifier(
    tokens={MCP_AUTH_TOKEN: {"sub": "poke", "client_id": "poke", "scopes": ["mcp"]}},
)
mcp = FastMCP("Dota Digital Prison", auth=auth)


def write_prison_state(unlocked_until: float) -> None:
    tmp = PRISON_STATE.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"unlocked_until": unlocked_until}),
        encoding="utf-8",
    )
    tmp.replace(PRISON_STATE)


@mcp.tool
def get_prison_status() -> str:
    """Return prison lock state and any active CODE GREEN alert as JSON."""
    return _json(prison_status())


@mcp.tool
def get_code_green() -> str:
    """Return the active CODE GREEN alert as JSON, or inactive status."""
    log_tool_call("get_code_green", {})
    alert = read_code_green()
    if alert is None:
        return _json({"active": False, "code_green": None})
    return _json({"active": True, "code_green": alert})


@mcp.tool
def wait_for_code_green(timeout_seconds: int = 120) -> str:
    """Block until CODE GREEN is active or timeout. Prefer this over polling get_prison_status."""
    timeout_seconds = max(1, min(timeout_seconds, 300))
    started = time.time()
    deadline = started + timeout_seconds

    while time.time() < deadline:
        alert = read_code_green()
        if alert is not None:
            return _json(
                {
                    "active": True,
                    "timed_out": False,
                    "waited_seconds": round(time.time() - started, 2),
                    "code_green": alert,
                    "prison": prison_status(),
                }
            )
        time.sleep(0.5)

    return _json(
        {
            "active": False,
            "timed_out": True,
            "waited_seconds": round(time.time() - started, 2),
            "code_green": None,
            "prison": prison_status(),
        }
    )


@mcp.tool
def get_gsi_trace_tail(limit: int = 20) -> str:
    """Return the last N lines from the latest session gsi_trace.jsonl."""
    limit = max(1, min(limit, 100))
    session = load_latest_session()
    if session is None or not session.gsi_trace.is_file():
        return _json(
            {
                "entries": [],
                "message": "No session logs yet — start watcher (launch_prison.bat or python watcher.py).",
            }
        )

    lines = session.gsi_trace.read_text(encoding="utf-8").splitlines()
    tail = lines[-limit:]
    parsed: list[Any] = []
    for line in tail:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            parsed.append({"raw": line})

    return _json(
        {
            "session_id": session.session_id,
            "session_dir": str(session.session_dir),
            "trace_file": str(session.gsi_trace),
            "count": len(parsed),
            "entries": parsed,
        }
    )


@mcp.tool
def execute_code_green(dry_run: bool = False) -> str:
    """Terminate Dota 2 and clear CODE GREEN. Use dry_run=true for pipeline tests."""
    log_tool_call("execute_code_green", {"dry_run": dry_run})
    _ok, payload = run_execute_code_green(dry_run=dry_run)
    log_tool_call("execute_code_green", {"dry_run": dry_run}, result_ok=_ok)
    return _json(payload)


@mcp.tool
def pardon_code_green(notes: str = "") -> str:
    """Clear CODE GREEN without terminating Dota. Resets feed strikes in the watcher."""
    log_tool_call("pardon_code_green", {"notes": notes[:80] if notes else ""})
    _ok, payload = run_pardon_code_green(notes)
    return _json(payload)


@mcp.tool
def unlock_dota(hours: int = 2) -> str:
    """Grant temporary prison unlock (disables enforcement until expiry)."""
    unlocked_until = time.time() + (hours * 3600)
    write_prison_state(unlocked_until)
    until_iso = datetime.fromtimestamp(unlocked_until, tz=timezone.utc).isoformat()
    return _json(
        {
            "ok": True,
            "unlocked_until": unlocked_until,
            "unlocked_until_utc": until_iso,
            "hours": hours,
        }
    )


@mcp.tool
def get_handshake_token() -> str:
    """Return the pending API handshake token. Use this EXACT value in poke_api_ack."""
    pending = read_handshake_pending()
    if pending is None:
        return _json({"ok": False, "token": None, "message": "No pending handshake."})
    return _json(
        {
            "ok": True,
            "token": pending.get("token"),
            "expires_utc": pending.get("expires_utc"),
            "message": "Pass this token verbatim to poke_api_ack(ping_token=...).",
        }
    )


@mcp.tool
def poke_api_ack(ping_token: str, message: str = "got your api call") -> str:
    """Acknowledge a Poke inbound API message. Call this when api-message/handshake arrives."""
    payload = write_poke_ack(ping_token, message, source="poke_mcp")
    matched = payload.get("token_matched")
    print(
        f"[MCP] poke_api_ack | token={ping_token!r} | matched={matched} | message={message!r}",
        file=sys.stderr,
        flush=True,
    )
    return _json({"ok": True, "ack": payload})


@mcp.tool
def get_poke_api_ack() -> str:
    """Return the latest poke_api_ack flag (proof Poke received api-message via MCP)."""
    ack = read_poke_ack()
    if ack is None:
        return _json({"ok": False, "ack": None, "message": "No ack yet."})
    return _json({"ok": True, "ack": ack})


@mcp.tool
def get_last_prison_violation() -> str:
    """Return the most recent logged violation as JSON."""
    entry = get_last_violation()
    return _json({"violation": entry})


@mcp.tool
def get_recent_prison_violations(limit: int = 10) -> str:
    """Return recent logged violations as JSON."""
    return _json({"violations": get_recent_events(limit)})


if __name__ == "__main__":
    suffix = MCP_AUTH_TOKEN[-4:]
    tool_names = [
        "get_prison_status",
        "get_code_green",
        "wait_for_code_green",
        "execute_code_green",
        "pardon_code_green",
        "unlock_dota",
        "poke_api_ack",
        "get_poke_api_ack",
        "get_handshake_token",
        "get_last_prison_violation",
        "get_recent_prison_violations",
        "get_gsi_trace_tail",
    ]
    print(
        f"MCP auth enabled. Token suffix: ...{suffix} "
        f"(Authorization header must match exactly)",
        file=sys.stderr,
    )
    print(f"MCP tools ({len(tool_names)}): {', '.join(tool_names)}", file=sys.stderr)
    mcp.run(transport="http", host=MCP_HOST, port=MCP_PORT)
