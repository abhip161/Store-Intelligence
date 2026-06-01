from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pipeline.schemas import Point


class CrossingDirection(StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


@dataclass(frozen=True)
class LineCrossing:
    direction: CrossingDirection
    previous_side: int
    current_side: int


def point_side(point: Point, line_start: Point, line_end: Point) -> int:
    """Return which side of a directed line a point is on.

    The sign is enough for threshold crossing. A small dead-band around zero
    prevents jitter on the line from producing duplicate entry/exit events.
    """

    value = (line_end.x - line_start.x) * (point.y - line_start.y) - (
        line_end.y - line_start.y
    ) * (point.x - line_start.x)
    epsilon = 1e-6
    if value > epsilon:
        return 1
    if value < -epsilon:
        return -1
    return 0


class EntryExitLine:
    def __init__(self, start: Point, end: Point, inbound_side: int = 1) -> None:
        if inbound_side not in (-1, 1):
            raise ValueError("inbound_side must be either 1 or -1")
        self.start = start
        self.end = end
        self.inbound_side = inbound_side

    def detect_crossing(self, previous: Point, current: Point) -> LineCrossing | None:
        previous_side = point_side(previous, self.start, self.end)
        current_side = point_side(current, self.start, self.end)

        if previous_side == 0 or current_side == 0 or previous_side == current_side:
            return None

        direction = (
            CrossingDirection.INBOUND
            if current_side == self.inbound_side
            else CrossingDirection.OUTBOUND
        )
        return LineCrossing(
            direction=direction,
            previous_side=previous_side,
            current_side=current_side,
        )
