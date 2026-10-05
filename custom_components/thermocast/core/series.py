"""Hourly aggregation of state sequences (e.g. from the HA recorder) – no HA imports."""
from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

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


def hourly_mean(series: StateSeries, hours: list[datetime], end: datetime) -> list[float | None]:
    """Time-weighted mean of numeric states per hour, up to ``end`` (later hours: None)."""
    return _hourly(series, hours, end, _float)


def hourly_fraction(
    series: StateSeries, hours: list[datetime], end: datetime, value_of: Callable[[str], float | None]
) -> list[float | None]:
    """Share of each hour in which ``value_of(state)`` is 1 (on)."""
    return _hourly(series, hours, end, value_of)
