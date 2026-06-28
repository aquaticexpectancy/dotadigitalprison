"""Dota 2 Digital Prison GSI watcher."""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from code_green import consume_strike_reset, raise_code_green
from events import append_event
from gsi_trace import append_trace, build_trace_record, init_trace_file, trace_path
from lane_map import is_on_lane
from session_log import start_session

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
LANE_GRIEF_EVAL_GAME_SECONDS = 600.0
LANE_PRESENCE_MIN = 0.38
LANE_GRIEF_MIN_POSITION_SAMPLES = 25
LANE_GRIEF_TRACK_START_GAME_SECONDS = 30.0

logger = logging.getLogger("dota_prison")

_vaporize_lock = threading.Lock()
_last_heartbeat_log = 0.0
_last_unlocked_notice = 0.0
_gsi_payload_count = 0
_cheese_flagged_matches: set[str] = set()


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


def request_code_green(reason: str, event: str, **details: Any) -> bool:
    """Raise CODE GREEN once per violation. Returns True if a new alert was created."""
    with _vaporize_lock:
        alert, created = raise_code_green(event, reason, details)

    if not created:
        return False

    append_event(event, reason, details)
    logger.warning(
        "CODE GREEN raised | event=%s | jailed_reason=%s | summary=%s — Poke decides execute or pardon",
        alert.get("event"),
        alert.get("jailed_reason"),
        alert.get("summary"),
    )
    return True


def maybe_flag_forbidden_hero(cache: GameStateCache, enforce: bool) -> None:
    if not enforce:
        return

    hero_name = cache.hero_name
    if not isinstance(hero_name, str) or hero_name not in FORBIDDEN_HEROES:
        return

    match_id = cache.match_id
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
    if created and match_id:
        _cheese_flagged_matches.add(match_id)


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
        self.on_lane: bool | None = None

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

        if self.xpos is not None and self.ypos is not None:
            self.on_lane = is_on_lane(self.xpos, self.ypos)
        else:
            self.on_lane = None

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


def expected_xpm(game_time_seconds: float, turbo: bool) -> float:
    minutes = game_time_seconds / 60.0
    baseline = 140.0 + minutes * 33.0
    return baseline * (1.35 if turbo else 1.0)


def expected_lhpm(game_time_seconds: float, turbo: bool) -> float:
    minutes = game_time_seconds / 60.0
    baseline = 1.8 + minutes * 0.38
    return baseline * (1.45 if turbo else 1.0)


class LaneGriefTracker:
    """Track lane presence, XP/min, and LH/min for the first 10 minutes."""

    def __init__(self) -> None:
        self._match_id: str | None = None
        self._evaluated = False
        self._position_samples = 0
        self._on_lane_samples = 0
        self._track_start_game_time: float | None = None
        self._start_xp: int | None = None
        self._start_last_hits: int | None = None
        self._last_xp: int | None = None
        self._last_last_hits: int | None = None

    def reset_match(self, match_id: str | None) -> None:
        self._match_id = match_id
        self._evaluated = False
        self._position_samples = 0
        self._on_lane_samples = 0
        self._track_start_game_time = None
        self._start_xp = None
        self._start_last_hits = None
        self._last_xp = None
        self._last_last_hits = None

    def _maybe_baseline(self, cache: GameStateCache) -> None:
        game_time = cache.game_time
        if game_time is None or game_time < LANE_GRIEF_TRACK_START_GAME_SECONDS:
            return
        if self._track_start_game_time is not None:
            return

        xp = cache.hero_xp
        last_hits = cache.last_hits
        if xp is None or last_hits is None:
            return

        self._track_start_game_time = game_time
        self._start_xp = xp
        self._start_last_hits = last_hits
        self._last_xp = xp
        self._last_last_hits = last_hits
        logger.info(
            "Lane grief tracking started at %s | baseline xp=%s lh=%s",
            cache.timer_label(),
            xp,
            last_hits,
        )

    def _sample_position(self, cache: GameStateCache) -> None:
        if cache.paused:
            return
        if cache.hero_alive is False:
            return

        if cache.xpos is None or cache.ypos is None:
            return

        self._position_samples += 1
        if cache.on_lane:
            self._on_lane_samples += 1

    def trace_snapshot(self, cache: GameStateCache | None = None) -> dict[str, Any]:
        lane_pct: float | None = None
        if self._position_samples > 0:
            lane_pct = round(self._on_lane_samples / self._position_samples, 3)

        missing: list[str] = []
        if self._track_start_game_time is None and cache is not None:
            if cache.hero_xp is None:
                missing.append("hero_xp")
            if cache.last_hits is None:
                missing.append("last_hits")
        if cache is not None:
            if cache.xpos is None:
                missing.append("xpos")
            if cache.ypos is None:
                missing.append("ypos")

        return {
            "evaluated": self._evaluated,
            "baseline_set": self._track_start_game_time is not None,
            "track_start_game_time": self._track_start_game_time,
            "position_samples": self._position_samples,
            "on_lane_samples": self._on_lane_samples,
            "lane_presence_pct": lane_pct,
            "start_xp": self._start_xp,
            "start_last_hits": self._start_last_hits,
            "last_xp": self._last_xp,
            "last_last_hits": self._last_last_hits,
            "missing_for_baseline": missing,
        }

    def _evaluate(
        self,
        cache: GameStateCache,
        flag_callback: Callable[..., None],
        enforce: bool,
    ) -> None:
        if self._evaluated:
            return
        self._evaluated = True

        game_time = cache.game_time or LANE_GRIEF_EVAL_GAME_SECONDS
        turbo = cache.gamemode == "Turbo"
        flags: list[str] = []
        metrics: dict[str, Any] = {
            "gamemode": cache.gamemode,
            "timer_label": cache.timer_label(),
            "match_id": cache.match_id,
            "game_time": game_time,
            "position_samples": self._position_samples,
        }

        if self._position_samples >= LANE_GRIEF_MIN_POSITION_SAMPLES:
            lane_pct = self._on_lane_samples / self._position_samples
            metrics["lane_presence_pct"] = round(lane_pct, 3)
            metrics["on_lane_samples"] = self._on_lane_samples
            if lane_pct < LANE_PRESENCE_MIN:
                flags.append("off_lane")
        else:
            metrics["lane_presence_pct"] = None
            logger.warning(
                "Lane grief: skipped off_lane check — only %s/%s position samples "
                "(GSI may not be sending hero.xpos/ypos — check session gsi_trace.jsonl)",
                self._position_samples,
                LANE_GRIEF_MIN_POSITION_SAMPLES,
            )

        elapsed_minutes: float | None = None
        avg_xpm: float | None = None
        avg_lhpm: float | None = None

        if (
            self._track_start_game_time is not None
            and self._start_xp is not None
            and self._start_last_hits is not None
            and self._last_xp is not None
            and self._last_last_hits is not None
        ):
            elapsed_seconds = game_time - self._track_start_game_time
            if elapsed_seconds >= 120.0:
                elapsed_minutes = elapsed_seconds / 60.0
                xp_gained = max(0, self._last_xp - self._start_xp)
                lh_gained = max(0, self._last_last_hits - self._start_last_hits)
                avg_xpm = xp_gained / elapsed_minutes
                avg_lhpm = lh_gained / elapsed_minutes
                metrics["avg_xpm"] = round(avg_xpm, 1)
                metrics["avg_lhpm"] = round(avg_lhpm, 2)
                metrics["expected_xpm"] = round(expected_xpm(game_time, turbo), 1)
                metrics["expected_lhpm"] = round(expected_lhpm(game_time, turbo), 2)
                metrics["xp_gained"] = xp_gained
                metrics["last_hits_gained"] = lh_gained
                metrics["track_minutes"] = round(elapsed_minutes, 1)

                if avg_xpm < expected_xpm(game_time, turbo):
                    flags.append("low_xp")
                if avg_lhpm < expected_lhpm(game_time, turbo):
                    flags.append("low_last_hits")

        locked_note = "" if enforce else " [UNLOCKED — log only]"
        logger.info(
            "Lane grief 10-min report%s | flags=%s | lane=%s | xpm=%s | lhpm=%s",
            locked_note,
            flags or "none",
            metrics.get("lane_presence_pct"),
            metrics.get("avg_xpm"),
            metrics.get("avg_lhpm"),
        )

        if not flags:
            if self._track_start_game_time is None:
                logger.warning(
                    "Lane grief: no XP/LH baseline set by 10 min — "
                    "hero.xp or player.last_hits never arrived in GSI"
                )
            return

        metrics["flags"] = flags
        flag_callback(
            "Lane grief check failed in the first 10 minutes.",
            "lane_grief",
            **metrics,
        )

    def update(
        self,
        cache: GameStateCache,
        flag_callback: Callable[..., None],
        enforce: bool,
    ) -> None:
        if self._evaluated:
            return

        game_time = cache.game_time
        if game_time is None:
            return

        if cache.match_id and self._match_id and cache.match_id != self._match_id:
            self.reset_match(cache.match_id)
        if cache.match_id:
            self._match_id = cache.match_id

        self._maybe_baseline(cache)

        if cache.hero_xp is not None:
            self._last_xp = cache.hero_xp
        if cache.last_hits is not None:
            self._last_last_hits = cache.last_hits

        if game_time <= LANE_GRIEF_EVAL_GAME_SECONDS:
            self._sample_position(cache)

        if game_time >= LANE_GRIEF_EVAL_GAME_SECONDS:
            self._evaluate(cache, flag_callback, enforce)


lane_grief_tracker = LaneGriefTracker()


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
        "state=%s | deaths=%s | lh=%s | xp=%s | xpm=%s | pos=%s | strikes=%s",
        _gsi_payload_count,
        locked,
        sections,
        cache.timer_label(),
        cache.gamemode or "?",
        cache.game_state or "?",
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


class GSIHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        global _gsi_payload_count

        body = self._read_body()
        self._send_ok()

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
        lane_grief_tracker.update(feed_tracker._cache, vaporize, enforce)

        append_trace(
            build_trace_record(
                seq=_gsi_payload_count,
                payload=payload,
                cache=feed_tracker._cache,
                lane_grief=lane_grief_tracker.trace_snapshot(feed_tracker._cache),
            )
        )

    def _read_body(self) -> bytes:
        length_header = self.headers.get("Content-Length")
        if not length_header:
            return b""

        try:
            length = int(length_header)
        except ValueError:
            logger.warning("Invalid Content-Length header: %r", length_header)
            return b""

        if length <= 0:
            return b""

        try:
            return self.rfile.read(length)
        except OSError as exc:
            logger.warning("Failed to read request body: %s", exc)
            return b""

    def _send_ok(self) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok")
        except OSError as exc:
            logger.warning("Failed to send response: %s", exc)


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
    logger.info(
        "Dota Digital Prison watcher on http://%s:%s/ | prison_locked=%s",
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

    server = HTTPServer((HOST, PORT), GSIHandler)

    def shutdown(_signum: int | None = None, _frame: Any | None = None) -> None:
        logger.info("Shutting down...")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, shutdown)

    try:
        server.serve_forever()
    finally:
        server.server_close()
        logger.info("Watcher stopped.")


if __name__ == "__main__":
    main()
