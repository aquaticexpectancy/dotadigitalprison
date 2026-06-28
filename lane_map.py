"""Dota 2 map lane geometry for GSI xpos/ypos checks.

GSI coordinates are world units on the default map. Lanes are approximated as
thick segments around the three creep paths (mid, bottom/safe, top/off).
Tune LANE_HALF_WIDTH or segment endpoints if checks feel too strict/loose.
"""

from __future__ import annotations

import math

# Distance from a lane centerline that still counts as "on lane".
LANE_HALF_WIDTH = 1650.0

# (x1, y1, x2, y2) — approximate creep paths, radiant bottom-left to dire top-right.
LANE_SEGMENTS: tuple[tuple[float, float, float, float], ...] = (
    (-6600.0, -6400.0, 6600.0, 6400.0),   # mid
    (-7600.0, -7000.0, 7000.0, -4600.0),   # bottom / safe lane corridor
    (-7000.0, -7600.0, -4600.0, 7000.0),   # top / off lane corridor
)

# Fountain / ancient areas count as on-lane (early game, respawn).
BASE_ZONES: tuple[tuple[float, float, float, float], ...] = (
    (-8200.0, -8200.0, -4800.0, -4800.0),  # radiant base
    (4800.0, 4800.0, 8200.0, 8200.0),       # dire base
)


def _dist_point_to_segment(
    px: float, py: float, x1: float, y1: float, x2: float, y2: float
) -> float:
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - x1, py - y1)

    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _in_rect(px: float, py: float, x_min: float, y_min: float, x_max: float, y_max: float) -> bool:
    return x_min <= px <= x_max and y_min <= py <= y_max


def is_on_lane(x: float, y: float) -> bool:
    """Return True if world position is near any lane or team base."""
    for x1, y1, x2, y2 in LANE_SEGMENTS:
        if _dist_point_to_segment(x, y, x1, y1, x2, y2) <= LANE_HALF_WIDTH:
            return True

    for x_min, y_min, x_max, y_max in BASE_ZONES:
        if _in_rect(x, y, x_min, y_min, x_max, y_max):
            return True

    return False
