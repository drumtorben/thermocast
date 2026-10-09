"""Hourly aggregation of state sequences (e.g. from the HA recorder) – no HA imports."""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta
from itertools import pairwise

StateSeries = list[tuple[datetime, str]]
HOUR = timedelta(hours=1)


def _float(value: str) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _segments(series: StateSeries, start: datetime, end: datetime) -> Iterator[tuple[float, str]]:
    """(duration in s, state) of every piece of [start, end)."""
    for k, (t, state) in enumerate(series):
        t1 = series[k + 1][0] if k + 1 < len(series) else end
        a, b = max(t, start), min(t1, end)
        if b > a:
            yield (b - a).total_seconds(), state


def _hourly(
    series: StateSeries, hours: list[datetime], end: datetime, value_of: Callable[[str], float | None]
) -> list[float | None]:
    out: list[float | None] = []
    for h in hours:
        b = min(h + HOUR, end)
        total = weight = 0.0
        if b > h:
            for seconds, state in _segments(series, h, b):
                v = value_of(state)
                if v is not None:
                    total += v * seconds
                    weight += seconds
        out.append(round(total / weight, 3) if weight > 0 else None)
    return out


def mask_off(series: StateSeries, mask: StateSeries, is_on: Callable[[str], float | None]) -> StateSeries:
    """``series`` with every period in which ``mask`` is on forced to "off".

    E.g. boiler pump minus DHW charging: on a combi boiler the same pump runs for hot water.
    """
    points = sorted({t for t, _ in series} | {t for t, _ in mask})
    out: StateSeries = []
    i = j = 0
    cur: str | None = None
    masked = False
    for t in points:
        while i < len(series) and series[i][0] <= t:
            cur = series[i][1]
            i += 1
        while j < len(mask) and mask[j][0] <= t:
            masked = is_on(mask[j][1]) == 1.0
            j += 1
        if cur is None:
            continue
        state = "off" if masked and is_on(cur) is not None else cur
        if not out or out[-1][1] != state:
            out.append((t, state))
    return out


def hourly_increase(series: StateSeries, hours: list[datetime], end: datetime) -> list[float | None]:
    """Increase of a counter (e.g. burner starts) per hour; drops (counter reset) count as 0."""
    values = [(t, v) for t, s in series if (v := _float(s)) is not None]
    out: list[float | None] = []
    for h in hours:
        b = min(h + HOUR, end)
        if b <= h:
            out.append(None)
            continue
        before = [v for t, v in values if t < h]
        inside = [v for t, v in values if h <= t < b]
        seq = before[-1:] + inside
        if not seq:
            out.append(None)
            continue
        out.append(float(sum(max(0.0, y - x) for x, y in pairwise(seq))))
    return out


def hourly_mean(series: StateSeries, hours: list[datetime], end: datetime) -> list[float | None]:
    """Time-weighted mean of numeric states per hour, up to ``end`` (later hours: None)."""
    return _hourly(series, hours, end, _float)


def hourly_fraction(
    series: StateSeries, hours: list[datetime], end: datetime, value_of: Callable[[str], float | None]
) -> list[float | None]:
    """Share of each hour in which ``value_of(state)`` is 1 (on)."""
    return _hourly(series, hours, end, value_of)


def _hour_of(t: datetime) -> datetime:
    return t.replace(minute=0, second=0, microsecond=0)


class HourlyIntegral:
    """Live time-weighted hourly means of a signal fed at its changes: a value holds until the next one.

    E.g. the heating proxy of a cycling burner – four samples per hour hit or miss its short runs.
    A value holds at most ``max_hold`` (a silent feed is a gap, not a constant); None is a gap too.
    """

    def __init__(self, max_hold: timedelta = timedelta(minutes=30), keep_hours: int = 3) -> None:
        self._max_hold = max_hold
        self._keep = keep_hours
        self._value: float | None = None
        self._since: datetime | None = None
        self._hours: dict[datetime, list[float]] = {}  # hour start -> [value · s, s]

    def set(self, now: datetime, value: float | None) -> None:
        if self._since is not None and now < self._since:
            return  # clock went backwards: keep the running segment
        if self._since is not None and self._value is not None:
            t, end = self._since, min(now, self._since + self._max_hold)
            while t < end:
                b = min(_hour_of(t) + HOUR, end)
                acc = self._hours.setdefault(_hour_of(t), [0.0, 0.0])
                seconds = (b - t).total_seconds()
                acc[0] += self._value * seconds
                acc[1] += seconds
                t = b
        self._value, self._since = value, now
        oldest = _hour_of(now) - self._keep * HOUR
        self._hours = {h: acc for h, acc in self._hours.items() if h >= oldest}

    def mean(self, hour: datetime, min_covered: timedelta = timedelta(0)) -> float | None:
        """Mean over the covered part of ``hour`` (None: less than ``min_covered``, or nothing, covered)."""
        acc = self._hours.get(hour)
        if not acc or acc[1] <= 0 or acc[1] < min_covered.total_seconds():
            return None
        return acc[0] / acc[1]

    def to_dict(self) -> dict[str, list[float]]:
        return {h.isoformat(): list(acc) for h, acc in self._hours.items()}

    def load(self, data: dict[str, list[float]] | None) -> None:
        """Restore the collected hours; the running value restarts with the next feed (no hold across a restart)."""
        self._hours = {datetime.fromisoformat(h): [float(acc[0]), float(acc[1])] for h, acc in (data or {}).items()}
