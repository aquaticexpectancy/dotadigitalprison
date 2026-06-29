"""Dota 2 Digital Prison GSI watcher — FastAPI async receiver."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse
import uvicorn

from code_green import (
    clear_stale_code_green,
    consume_strike_reset,
    get_code_green,
    raise_code_green,
    violation_meta,
)
from poke_http import close_http_client, get_http_client
from poke_notify import notify_code_green
from events import append_event
from gsi_trace import append_trace, build_trace_record, init_trace_file, trace_path
from session_log import start_session
from poke_pipeline import pipeline_status

ROOT = Path(__file__).resolve().parent
PRISON_STATE = ROOT / "prison_state.json"
FORBIDDEN_HEROES = frozenset(
    {
        "npc_dota_hero_meepo",
        "npc_dota_hero_huskar",
        "npc_dota_hero_broodmother",
    }
)
HOST = "127.0.0.1"
PORT = 3000
FEED_STRIKES_REQUIRED = 5
FEED_STRIKE_RESET_SECONDS = 60.0
HEARTBEAT_LOG_SECONDS = 30.0

logger = logging.getLogger("dota_prison")

_vaporize_lock = threading.Lock()
_last_heartbeat_log = 0.0
_last_unlocked_notice = 0.0
_gsi_payload_count = 0
_cheese_flagged_matches: set[str] = set()
_feed_supplement_notified: set[str] = set()


def is_locked() -> bool:
    try:
        raw = PRISON_STATE.read_text(encoding="utf-8")
        data = json.loads(raw)
        unlocked_until = data["unlocked_until"]
        if not isinstance(unlocked_until, (int, float)):
            return True
        return time.time() > float(unlocked_until)
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return True


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def format_hud_time(clock_time: float) -> str:
    total = int(clock_time)
    sign = "-" if total < 0 else ""
    total = abs(total)
    return f"{sign}{total // 60}:{total % 60:02d}"


def resolve_gamemode(map_data: dict[str, Any]) -> str:
    custom = map_data.get("customgamename")
    if isinstance(custom, str) and custom.strip():
        return custom.strip()

    for field in ("radiant_ward_purchase_cooldown", "dire_ward_purchase_cooldown"):
        ward_cd = as_float(map_data.get(field))
        if ward_cd is None:
            continue
        if ward_cd <= 130:
            return "Turbo"
        if ward_cd >= 200:
            return "All Pick / Ranked"

    map_name = map_data.get("name")
    if isinstance(map_name, str) and map_name.strip():
        if map_name.lower() == "dota":
            return "Standard Dota"
        return map_name.strip()

    return "Unknown"


def noop_vaporize(reason: str, event: str, **details: Any) -> None:
    logger.info("Feed threshold hit but prison is unlocked — no enforcement (%s)", event)


def maybe_notify_feed_supplement(
    event: str, reason: str, details: dict[str, Any]
) -> bool:
    """Notify Poke about feeding once when cheese CODE GREEN is already active."""
    match_id = details.get("match_id")
    key = str(match_id) if match_id is not None else "_unknown"
    if key in _feed_supplement_notified:
        return False
    _feed_supplement_notified.add(key)

    meta = violation_meta(event, details)
    append_event(event, reason, details)
    alert: dict[str, Any] = {
        "active": True,
        "code": "green",
        "timestamp": time.time(),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "event": meta["event"],
        "jailed_reason": meta["jailed_reason"],
        "label": meta["label"],
        "summary": meta["summary"],
        "reason": reason,
        "details": details,
        "supplement": True,
    }
    logger.warning(
        "CODE GREEN feed supplement notify | match=%s strikes=%s — primary alert still active",
        match_id,
        details.get("strikes"),
    )
    notify_code_green(alert)
    return True


def request_code_green(reason: str, event: str, **details: Any) -> bool:
    """Raise CODE GREEN once per violation. Returns True if a new alert was created."""
    with _vaporize_lock:
        alert, created = raise_code_green(event, reason, details)

    if not created:
        if event == "excessive_feeding":
            maybe_notify_feed_supplement(event, reason, details)
        return False

    append_event(event, reason, details)
    logger.warning(
        "CODE GREEN raised | event=%s | jailed_reason=%s | summary=%s — Poke decides execute or pardon",
        alert.get("event"),
        alert.get("jailed_reason"),
        alert.get("summary"),
    )
    notify_code_green(alert)
    return True


def maybe_flag_forbidden_hero(cache: GameStateCache, enforce: bool) -> None:
    hero_name = cache.hero_name
    match_id = cache.match_id
    if not enforce:
        return

    if not isinstance(hero_name, str):
        return
    if hero_name not in FORBIDDEN_HEROES:
        return

    if match_id and match_id in _cheese_flagged_matches:
        return

    created = request_code_green(
        "Vaporizing client: unauthorized cheese draft.",
        "forbidden_hero_draft",
        hero=hero_name,
        gamemode=cache.gamemode,
        timer_label=cache.timer_label(),
        match_id=match_id,
    )
    if created:
        if match_id:
            _cheese_flagged_matches.add(match_id)
        logger.warning(
            "CHEESE detected | hero=%s | match=%s — CODE GREEN raised, notifying Poke",
            hero_name,
            match_id,
        )
        return

    existing = get_code_green()
    if existing and match_id:
        _cheese_flagged_matches.add(match_id)
    logger.info(
        "CHEESE hero=%s match=%s — already flagged (code_green.json still active; "
        "pardon/execute via MCP or delete code_green.json to re-test)",
        hero_name,
        match_id,
    )


class GameStateCache:
    def __init__(self) -> None:
        self.match_id: str | None = None
        self.clock_time: float | None = None
        self.game_time: float | None = None
        self.game_state: str | None = None
        self.gamemode: str | None = None
        self.paused: bool = False
        self.last_deaths_seen: int | None = None
        self.last_hits: int | None = None
        self.xpm: int | None = None
        self.gpm: int | None = None
        self.hero_name: str | None = None
        self.hero_level: int | None = None
        self.hero_xp: int | None = None
        self.hero_alive: bool | None = None
        self.xpos: float | None = None
        self.ypos: float | None = None

    def update(self, payload: dict[str, Any]) -> None:
        map_data = payload.get("map")
        if isinstance(map_data, dict):
            match_id = map_data.get("matchid")
            if match_id is not None:
                self.match_id = str(match_id)

            clock_time = as_float(map_data.get("clock_time"))
            if clock_time is not None:
                self.clock_time = clock_time

            game_time = as_float(map_data.get("game_time"))
            if game_time is not None:
                self.game_time = game_time

            game_state = map_data.get("game_state")
            if isinstance(game_state, str):
                self.game_state = game_state

            paused = map_data.get("paused")
            if isinstance(paused, bool):
                self.paused = paused

            self.gamemode = resolve_gamemode(map_data)

        player = payload.get("player")
        if isinstance(player, dict):
            deaths = as_int(player.get("deaths"))
            if deaths is not None:
                self.last_deaths_seen = deaths

            last_hits = as_int(player.get("last_hits"))
            if last_hits is not None:
                self.last_hits = last_hits

            xpm = as_int(player.get("xpm"))
            if xpm is not None:
                self.xpm = xpm

            gpm = as_int(player.get("gpm"))
            if gpm is not None:
                self.gpm = gpm

        hero = payload.get("hero")
        if isinstance(hero, dict):
            hero_name = hero.get("name")
            if isinstance(hero_name, str):
                self.hero_name = hero_name

            level = as_int(hero.get("level"))
            if level is not None:
                self.hero_level = level

            xp = as_int(hero.get("xp"))
            if xp is not None:
                self.hero_xp = xp

            alive = hero.get("alive")
            if isinstance(alive, bool):
                self.hero_alive = alive

            xpos = as_float(hero.get("xpos"))
            if xpos is not None:
                self.xpos = xpos

            ypos = as_float(hero.get("ypos"))
            if ypos is not None:
                self.ypos = ypos

    def timer_seconds(self) -> float | None:
        if self.game_time is not None:
            return self.game_time
        return self.clock_time

    def timer_label(self) -> str:
        if self.clock_time is not None and self.game_time is not None:
            return f"HUD {format_hud_time(self.clock_time)} (game_time {self.game_time:.1f}s)"
        if self.clock_time is not None:
            return f"HUD {format_hud_time(self.clock_time)}"
        if self.game_time is not None:
            return f"game_time {self.game_time:.1f}s"
        return "timer unknown"

class FeedTracker:
    def __init__(self) -> None:
        self._cache = GameStateCache()
        self._tracked_match_id: str | None = None
        self._last_deaths: int | None = None
        self._strikes = 0
        self._last_death_at: float | None = None
        self._last_game_state: str | None = None

    def reset_strikes(self) -> None:
        self._strikes = 0
        self._last_death_at = None

    def _clear_strikes(self, reason: str) -> None:
        if self._strikes == 0:
            return
        logger.info(
            "Feed strikes cleared (was %d/%d) — %s",
            self._strikes,
            FEED_STRIKES_REQUIRED,
            reason,
        )
        self._strikes = 0
        self._last_death_at = None

    def _expire_strikes_if_idle(self) -> None:
        if self._strikes == 0 or self._last_death_at is None:
            return
        idle = time.time() - self._last_death_at
        if idle >= FEED_STRIKE_RESET_SECONDS:
            self._clear_strikes(
                f"{idle:.1f}s passed without dying ({self._cache.timer_label()})"
            )

    def _record_death(
        self,
        total_deaths: int,
        vaporize: Callable[..., None],
        enforce: bool,
    ) -> None:
        now = time.time()
        mode = self._cache.gamemode or "Unknown"
        timer_label = self._cache.timer_label()
        locked_note = "" if enforce else " [UNLOCKED — log only]"

        if self._last_death_at is not None:
            gap = now - self._last_death_at
            if gap >= FEED_STRIKE_RESET_SECONDS:
                self._clear_strikes(
                    f"{gap:.1f}s since last death — window expired before this death"
                )
            elif gap > 0:
                logger.info(
                    "Strike timer reset — died %.1fs after last death "
                    "(<%ss keeps strike count)",
                    gap,
                    FEED_STRIKE_RESET_SECONDS,
                )

        self._strikes += 1
        self._last_death_at = now

        logger.info(
            "User died at %s | mode: %s%s — strike %d/%d "
            "(%.0fs without dying resets count to 0)",
            timer_label,
            mode,
            locked_note,
            self._strikes,
            FEED_STRIKES_REQUIRED,
            FEED_STRIKE_RESET_SECONDS,
        )

        if self._strikes < FEED_STRIKES_REQUIRED:
            logger.info(
                "%d more strike(s) before vaporize — stay alive %.0fs to clear strikes",
                FEED_STRIKES_REQUIRED - self._strikes,
                FEED_STRIKE_RESET_SECONDS,
            )
            return

        logger.warning(
            "Feed threshold reached in %s: %d strikes%s",
            mode,
            self._strikes,
            locked_note,
        )
        vaporize(
            "Vaporizing client: excessive feeding detected.",
            "excessive_feeding",
            strikes=self._strikes,
            strikes_required=FEED_STRIKES_REQUIRED,
            strike_reset_seconds=FEED_STRIKE_RESET_SECONDS,
            gamemode=mode,
            timer_label=timer_label,
            match_id=self._tracked_match_id,
            clock_time=self._cache.clock_time,
            game_time=self._cache.game_time,
        )

    def update(
        self,
        payload: dict[str, Any],
        vaporize: Callable[..., None],
        enforce: bool,
    ) -> None:
        self._cache.update(payload)

        if self._cache.game_state != self._last_game_state:
            if self._cache.game_state is not None:
                logger.info("Game state -> %s", self._cache.game_state)
            self._last_game_state = self._cache.game_state

        if self._cache.match_id and self._tracked_match_id:
            if self._cache.match_id != self._tracked_match_id:
                logger.info(
                    "New match %s | mode: %s | %s",
                    self._cache.match_id,
                    self._cache.gamemode or "Unknown",
                    self._cache.timer_label(),
                )
                self._strikes = 0
                self._last_death_at = None
                self._last_deaths = None

        if self._cache.match_id:
            self._tracked_match_id = self._cache.match_id

        self._expire_strikes_if_idle()

        player = payload.get("player")
        if not isinstance(player, dict):
            return

        deaths = as_int(player.get("deaths"))
        if deaths is None:
            raw_deaths = player.get("deaths")
            if raw_deaths is not None:
                logger.warning(
                    "Could not parse player.deaths=%r (type %s)",
                    raw_deaths,
                    type(raw_deaths).__name__,
                )
            return

        timer_label = self._cache.timer_label()

        if self._last_deaths is None:
            self._last_deaths = deaths
            logger.info(
                "Death tracker baseline: %s deaths at %s | mode: %s | state: %s",
                deaths,
                timer_label,
                self._cache.gamemode or "Unknown",
                self._cache.game_state,
            )
            return

        if deaths < self._last_deaths:
            logger.info(
                "Death count dropped %s -> %s (new game/reset?) — rebaselining",
                self._last_deaths,
                deaths,
            )
            self._last_deaths = deaths
            self._strikes = 0
            self._last_death_at = None
            return

        if deaths == self._last_deaths:
            return

        logger.info(
            "Death count increased %s -> %s at %s | state=%s",
            self._last_deaths,
            deaths,
            timer_label,
            self._cache.game_state or "?",
        )

        for death_number in range(self._last_deaths + 1, deaths + 1):
            self._record_death(death_number, vaporize, enforce)

        self._last_deaths = deaths


feed_tracker = FeedTracker()


def log_gsi_heartbeat(payload: dict[str, Any], locked: bool) -> None:
    global _last_heartbeat_log, _last_unlocked_notice

    now = time.time()
    cache = feed_tracker._cache

    if not locked and now - _last_unlocked_notice >= HEARTBEAT_LOG_SECONDS:
        _last_unlocked_notice = now
        logger.info(
            "Prison UNLOCKED — logging deaths only, no vaporize "
            "(delete prison_state.json or wait for expiry to enforce)"
        )

    if now - _last_heartbeat_log < HEARTBEAT_LOG_SECONDS:
        return

    _last_heartbeat_log = now
    sections = [key for key in ("map", "player", "hero", "provider") if key in payload]
    logger.info(
        "GSI heartbeat #%s | locked=%s | sections=%s | %s | mode=%s | "
        "state=%s | hero=%s | deaths=%s | lh=%s | xp=%s | xpm=%s | pos=%s | strikes=%s",
        _gsi_payload_count,
        locked,
        sections,
        cache.timer_label(),
        cache.gamemode or "?",
        cache.game_state or "?",
        cache.hero_name or "?",
        cache.last_deaths_seen if cache.last_deaths_seen is not None else "?",
        cache.last_hits if cache.last_hits is not None else "?",
        cache.hero_xp if cache.hero_xp is not None else "?",
        cache.xpm if cache.xpm is not None else "?",
        (
            f"{cache.xpos:.0f},{cache.ypos:.0f}"
            if cache.xpos is not None and cache.ypos is not None
            else "?"
        ),
        feed_tracker._strikes,
    )


async def process_gsi_payload(body: bytes) -> None:
    global _gsi_payload_count

    if not body:
        logger.debug("Empty GSI POST body")
        return

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        logger.warning("Malformed GSI JSON: %s", exc)
        return

    if not isinstance(payload, dict):
        logger.warning("GSI payload is not a JSON object")
        return

    _gsi_payload_count += 1
    locked = is_locked()
    log_gsi_heartbeat(payload, locked)

    if consume_strike_reset():
        feed_tracker.reset_strikes()
        logger.info("Feed strikes reset by Poke pardon/execute.")

    feed_tracker._cache.update(payload)
    vaporize = request_code_green if locked else noop_vaporize
    enforce = locked

    maybe_flag_forbidden_hero(feed_tracker._cache, enforce)
    feed_tracker.update(payload, vaporize, enforce)

    append_trace(
        build_trace_record(
            seq=_gsi_payload_count,
            payload=payload,
            cache=feed_tracker._cache,
        )
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await get_http_client()
    yield
    await close_http_client()


app = FastAPI(lifespan=lifespan)


@app.post("/")
async def gsi_post(request: Request) -> PlainTextResponse:
    body = await request.body()
    asyncio.create_task(process_gsi_payload(body))
    return PlainTextResponse("ok")


def main() -> None:
    session = start_session()
    init_trace_file(session.gsi_trace)

    log_level = os.environ.get("DOTA_PRISON_LOG", "INFO").upper()
    log_format = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format=log_format,
    )

    file_handler = logging.FileHandler(session.watcher_log, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(log_format))
    logger.addHandler(file_handler)

    locked = is_locked()
    clear_stale_code_green()
    pipe = pipeline_status()
    if not pipe.get("mcp_port_open"):
        logger.warning("MCP server not listening on :5000 — launch_prison.bat MCP window?")
    elif pipe.get("poke_mcp_likely_stale"):
        ngrok = pipe.get("ngrok") or {}
        logger.warning(
            "ngrok tunnel up (%s) but zero HTTP hits — Poke MCP URL may be stale in Kitchen",
            ngrok.get("public_url"),
        )
    logger.info(
        "Dota Digital Prison watcher (FastAPI) on http://%s:%s/ | prison_locked=%s",
        HOST,
        PORT,
        locked,
    )
    logger.info("Session folder: %s", session.session_dir)
    logger.info("GSI trace log: %s", trace_path())
    logger.info("Watcher text log: %s", session.watcher_log)
    if not locked:
        logger.info(
            "Prison is UNLOCKED — you will see death logs but no vaporize until locked"
        )

    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level=log_level.lower(),
        access_log=False,
    )


if __name__ == "__main__":
    main()
