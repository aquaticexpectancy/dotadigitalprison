"""Poke/MCP pipeline health — local checks only (no external auth probes)."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
NGROK_DOMAIN_FILE = ROOT / ".ngrok_domain"
MCP_PORT = 5000


def mcp_port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", MCP_PORT), timeout=1.0):
            return True
    except OSError:
        return False


def ngrok_tunnel_status() -> dict[str, Any]:
    """Read ngrok agent API on localhost:4040."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        return {"running": False, "error": str(exc)}

    tunnels = data.get("tunnels") or []
    mcp_tunnels = [
        t
        for t in tunnels
        if str((t.get("config") or {}).get("addr", "")).endswith(f":{MCP_PORT}")
        or str((t.get("config") or {}).get("addr", "")) == f"http://localhost:{MCP_PORT}"
    ]
    if not mcp_tunnels:
        return {"running": True, "mcp_tunnel": False, "tunnel_count": len(tunnels)}

    tunnel = mcp_tunnels[0]
    metrics = tunnel.get("metrics") or {}
    http = metrics.get("http") or {}
    return {
        "running": True,
        "mcp_tunnel": True,
        "public_url": tunnel.get("public_url"),
        "http_requests_total": http.get("count", 0),
    }


def expected_ngrok_domain() -> str:
    if NGROK_DOMAIN_FILE.is_file():
        return NGROK_DOMAIN_FILE.read_text(encoding="utf-8").strip()
    return ""


def pipeline_status() -> dict[str, Any]:
    ngrok = ngrok_tunnel_status()
    expected = expected_ngrok_domain()
    public = str(ngrok.get("public_url") or "")
    domain_ok = bool(expected and expected in public) if public else False
    return {
        "mcp_port_open": mcp_port_open(),
        "ngrok": ngrok,
        "expected_ngrok_domain": expected or None,
        "ngrok_domain_matches": domain_ok,
        "poke_mcp_likely_stale": bool(
            ngrok.get("mcp_tunnel") and int(ngrok.get("http_requests_total") or 0) == 0
        ),
    }
