"""Local-first vaporization — taskkill without blocking the GSI event loop."""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("dota_prison.local_vaporize")


async def is_dota_running() -> bool:
    proc = await asyncio.create_subprocess_exec(
        "tasklist",
        "/FI",
        "IMAGENAME eq dota2.exe",
        "/NH",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _stderr = await proc.communicate()
    return b"dota2.exe" in stdout.lower()


async def vaporize_local() -> tuple[int, float]:
    """Kill dota2.exe locally. Returns (returncode, elapsed_ms)."""
    started = time.perf_counter()
    logger.warning("LOCAL vaporize — taskkill dota2.exe")
    proc = await asyncio.create_subprocess_exec(
        "taskkill",
        "/F",
        "/IM",
        "dota2.exe",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.warning("LOCAL vaporize complete rc=%s in %.1fms", rc, elapsed_ms)
    return rc, elapsed_ms
