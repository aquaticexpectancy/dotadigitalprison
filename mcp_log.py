"""Append-only MCP tool call log."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
MCP_LOG = ROOT / "logs" / "mcp_tools.jsonl"

_lock = threading.Lock()


def log_tool_call(tool: str, args: dict[str, Any] | None = None, **extra: Any) -> None:
    record = {
        "ts": time.time(),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args or {},
        **extra,
    }
    MCP_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with MCP_LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
