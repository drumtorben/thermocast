"""Vacation: a period (departure → return) in which every zone only holds a low floor, then heats up again
``lead_h`` hours before the return. Pure Python, no HA import."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

DEFAULT_VACATION_FLOOR = 15.0  # °C, house-wide; lowers each zone's base, never raises it
DEFAULT_VACATION_LEAD_H = 12.0  # hours before the return: back to the normal base temperature

AWAY = "away"
PREHEAT = "preheat"
Phase = Literal["away", "preheat"]


def _time(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if value else None
    except (TypeError, ValueError):
        return None


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class Vacation:
    start: datetime | None = None  # departure
    end: datetime | None = None  # return
    floor: float = DEFAULT_VACATION_FLOOR
    lead_h: float = DEFAULT_VACATION_LEAD_H

    def valid(self) -> bool:
        return self.start is not None and self.end is not None and self.end > self.start

    def preheat_from(self) -> datetime | None:
        """End of the lowering: the normal base temperature applies from here (not before the departure)."""
        if not self.valid():
            return None
        return max(self.start, self.end - timedelta(hours=max(0.0, self.lead_h)))

    def phase(self, when: datetime) -> Phase | None:
        """``away`` (lowered), ``preheat`` (normal base, no comfort) or None (normal operation)."""
        if not self.valid() or not self.start <= when < self.end:
            return None
        return PREHEAT if when >= self.preheat_from() else AWAY

    def relevant(self, now: datetime) -> bool:
        """Upcoming or running (a past period stays stored but is no longer shown)."""
        return self.valid() and now < self.end

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat() if self.end else None,
            "floor": self.floor,
            "lead_h": self.lead_h,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Vacation:
        d = d or {}
        return cls(
            start=_time(d.get("start")),
            end=_time(d.get("end")),
            floor=_float(d.get("floor"), DEFAULT_VACATION_FLOOR),
            lead_h=_float(d.get("lead_h"), DEFAULT_VACATION_LEAD_H),
        )
