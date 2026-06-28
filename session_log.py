"""Per-run session folders under logs/ for watcher + GSI trace files."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS_ROOT = ROOT / "logs"
LATEST_SESSION_FILE = LOGS_ROOT / "latest_session.json"


@dataclass(frozen=True)
class SessionPaths:
    session_id: str
    session_dir: Path
    gsi_trace: Path
    watcher_log: Path
    started_at_utc: str

    def as_dict(self) -> dict[str, str]:
        return {
            "session_id": self.session_id,
            "session_dir": str(self.session_dir),
            "gsi_trace": str(self.gsi_trace),
            "watcher_log": str(self.watcher_log),
            "started_at_utc": self.started_at_utc,
        }


def start_session() -> SessionPaths:
    LOGS_ROOT.mkdir(parents=True, exist_ok=True)

    started = datetime.now(timezone.utc)
    session_id = started.strftime("%Y-%m-%d_%H%M%S")
    session_dir = LOGS_ROOT / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    paths = SessionPaths(
        session_id=session_id,
        session_dir=session_dir,
        gsi_trace=session_dir / "gsi_trace.jsonl",
        watcher_log=session_dir / "watcher.log",
        started_at_utc=started.isoformat(),
    )

    meta = {
        **paths.as_dict(),
        "started_at": time.time(),
    }
    (session_dir / "session.json").write_text(
        json.dumps(meta, indent=2),
        encoding="utf-8",
    )
    LATEST_SESSION_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    paths.gsi_trace.touch(exist_ok=True)
    paths.watcher_log.touch(exist_ok=True)

    return paths


def load_latest_session() -> SessionPaths | None:
    if not LATEST_SESSION_FILE.is_file():
        return None

    try:
        data = json.loads(LATEST_SESSION_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None

    session_dir = Path(data.get("session_dir", ""))
    gsi_trace = Path(data.get("gsi_trace", session_dir / "gsi_trace.jsonl"))
    watcher_log = Path(data.get("watcher_log", session_dir / "watcher.log"))
    session_id = str(data.get("session_id", session_dir.name))

    if not session_dir.is_dir():
        return None

    return SessionPaths(
        session_id=session_id,
        session_dir=session_dir,
        gsi_trace=gsi_trace,
        watcher_log=watcher_log,
        started_at_utc=str(data.get("started_at_utc", "")),
    )
