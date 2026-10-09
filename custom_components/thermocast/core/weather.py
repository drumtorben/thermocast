"""Outdoor sensor offset: the forecast is moved onto the outdoor sensor the zone models learned with.

A sensor on a wall (e.g. the north side of a garage) reads warmer than the model's 2 m air temperature – at the
reference installation ~1.3 K, up to 2 K at noon (the wall warms up). The models learned the loss term with the
sensor, so the forecast has to speak the sensor's language. Learned per UTC hour of day (the sun's rhythm, no
daylight-saving jump), smoothed over the neighbouring hours.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, tzinfo
from typing import Any

from .forecast import Forecast

BIAS_ALPHA = 1.0 / 7.0  # one sample per hour of day and day -> follows about the last week
BIAS_MIN_WEIGHT = 1.0 - (1.0 - BIAS_ALPHA) ** 3  # until ~3 days are in, the offset is shrunk towards 0
BIAS_MAX = 5.0  # a larger difference is a broken sensor or a data gap, not an offset


def _zeros() -> list[float]:
    return [0.0] * 24


@dataclass
class OutdoorBias:
    """Exponential mean of (sensor − forecast) per UTC hour of day, weighted like a bias-corrected EMA."""

    num: list[float] = field(default_factory=_zeros)
    weight: list[float] = field(default_factory=_zeros)

    def update(self, when: datetime, measured: float | None, forecast: float | None) -> bool:
        """One closed hour. Returns False if the pair was not usable."""
        if measured is None or forecast is None or not (math.isfinite(measured) and math.isfinite(forecast)):
            return False
        diff = measured - forecast
        if abs(diff) > BIAS_MAX:
            return False
        h = when.astimezone(UTC).hour
        self.num[h] = (1.0 - BIAS_ALPHA) * self.num[h] + BIAS_ALPHA * diff
        self.weight[h] = (1.0 - BIAS_ALPHA) * self.weight[h] + BIAS_ALPHA
        return True

    def _raw(self, h: int) -> tuple[float, float]:
        w = self.weight[h]
        return (self.num[h] / max(w, BIAS_MIN_WEIGHT), w) if w > 0.0 else (0.0, 0.0)

    def profile(self) -> list[float]:
        """Offset per UTC hour (K), smoothed 1-2-1 over the neighbouring hours."""
        out = []
        for h in range(24):
            total = norm = 0.0
            for dh, k in ((-1, 1.0), (0, 2.0), (1, 1.0)):
                value, w = self._raw((h + dh) % 24)
                if w > 0.0:
                    total += k * value
                    norm += k
            out.append(total / norm if norm else 0.0)
        return out

    def offset(self, when: datetime) -> float:
        return self.profile()[when.astimezone(UTC).hour]

    def offset_fn(self) -> Callable[[datetime], float]:
        """``offset`` with the profile computed once (for a whole forecast)."""
        prof = self.profile()
        return lambda when: prof[when.astimezone(UTC).hour]

    @property
    def days(self) -> float:
        """Roughly how many days of samples are in (mean weight → equivalent days)."""
        w = sum(self.weight) / 24.0
        return math.log(1.0 - w) / math.log(1.0 - BIAS_ALPHA) if 0.0 < w < 1.0 else (0.0 if w <= 0.0 else math.inf)

    @property
    def empty(self) -> bool:
        return not any(self.weight)

    def learn(self, hours: Sequence[datetime], measured: Sequence[float | None], forecast: Sequence[float | None]) -> int:
        """Feed a history (oldest first). Returns the number of usable hours."""
        return sum(self.update(t, m, f) for t, m, f in zip(hours, measured, forecast))

    def to_dict(self) -> dict[str, Any]:
        return {"num": self.num, "weight": self.weight}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> OutdoorBias:
        if not d or len(d.get("num", [])) != 24 or len(d.get("weight", [])) != 24:
            return cls()
        return cls(num=[float(x or 0.0) for x in d["num"]], weight=[float(x or 0.0) for x in d["weight"]])


def _r(x: float | None, digits: int = 2) -> float | None:
    return round(x, digits) if x is not None and math.isfinite(x) else None


def weather_summary(bias: OutdoorBias, fc: Forecast | None, now: datetime, tz: tzinfo) -> dict[str, Any]:
    """Model tab: the sensor offset per local hour and the weather models' disagreement over the next 24 h."""
    prof = bias.profile()
    day = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    by_local = [0.0] * 24
    for h in range(24):
        t = day.replace(hour=h)
        by_local[t.astimezone(tz).hour] = prof[h]
    out: dict[str, Any] = {
        "offset_by_hour": None if bias.empty else [_r(v) for v in by_local],
        "offset_mean": None if bias.empty else _r(sum(prof) / 24.0),
        "days": None if bias.empty else _r(min(bias.days, 99.0), 1),
        "spread_mean": None, "spread_max": None, "sd_mean": None,
    }
    idx0 = fc.index_of(now) if fc else None
    if fc is not None and idx0 is not None:
        idx = range(idx0, min(idx0 + 24, len(fc.times)))
        spreads = [s for i in idx if i < len(fc.t_out_spread) and math.isfinite(s := fc.t_out_spread[i])]
        if spreads:
            out["spread_mean"], out["spread_max"] = _r(sum(spreads) / len(spreads)), _r(max(spreads))
        sds = [fc.t_out_sd(i) for i in idx]
        out["sd_mean"] = _r(sum(sds) / len(sds)) if sds else None
    return out
