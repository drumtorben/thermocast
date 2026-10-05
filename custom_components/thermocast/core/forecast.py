"""Open-Meteo client: outdoor temperature and irradiance per surface orientation.

No Home Assistant imports – pass any aiohttp-compatible ClientSession.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


def compass_to_open_meteo(azimuth: float) -> float:
    """Compass azimuth (0=N, 90=E, 180=S) -> Open-Meteo convention (0=S, -90=E, 90=W)."""
    a = (azimuth - 180.0) % 360.0
    return a - 360.0 if a >= 180.0 else a


@dataclass
class Forecast:
    """Hourly series, index aligned with ``times`` (UTC, hour starts)."""

    times: list[datetime] = field(default_factory=list)
    t_out: list[float] = field(default_factory=list)
    irr: dict[str, list[float]] = field(default_factory=dict)  # key -> W/m²
    fetched_at: datetime | None = None

    def index_of(self, when: datetime) -> int | None:
        hour = when.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        try:
            return self.times.index(hour)
        except ValueError:
            return None

    def irr_at(self, idx: int) -> dict[str, float]:
        return {k: (v[idx] if v[idx] is not None else 0.0) for k, v in self.irr.items()}

    def to_dict(self) -> dict[str, Any]:
        return {
            "times": [t.isoformat() for t in self.times],
            "t_out": self.t_out,
            "irr": self.irr,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


def _parse_times(raw: list[str]) -> list[datetime]:
    return [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in raw]


async def fetch_forecast(
    session: Any,
    latitude: float,
    longitude: float,
    orientations: Iterable[tuple[str, float, float]],  # (key, tilt, compass azimuth)
    past_days: int = 2,
    forecast_days: int = 3,
) -> Forecast:
    """Fetch temperature + global tilted irradiance for every unique orientation.

    Open-Meteo accepts one tilt/azimuth per request, so we issue one request per
    orientation (they are tiny and cached by the caller).
    """
    base = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
        "past_days": past_days,
        "forecast_days": forecast_days,
    }
    fc = Forecast(fetched_at=datetime.now(UTC))

    # temperature (+ horizontal irradiance as fallback key "0_0")
    data = await _get(session, {**base, "hourly": "temperature_2m,shortwave_radiation"})
    fc.times = _parse_times(data["hourly"]["time"])
    fc.t_out = [float(x) if x is not None else float("nan") for x in data["hourly"]["temperature_2m"]]
    fc.irr["0_0"] = [float(x or 0.0) for x in data["hourly"]["shortwave_radiation"]]

    seen: set[str] = {"0_0"}
    for key, tilt, azimuth in orientations:
        if key in seen:
            continue
        seen.add(key)
        params = {
            **base,
            "hourly": "global_tilted_irradiance",
            "tilt": round(tilt, 1),
            "azimuth": round(compass_to_open_meteo(azimuth), 1),
        }
        d = await _get(session, params)
        fc.irr[key] = [float(x or 0.0) for x in d["hourly"]["global_tilted_irradiance"]]
    return fc


async def _get(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    async with session.get(OPEN_METEO_URL, params=params, timeout=30) as resp:
        resp.raise_for_status()
        return await resp.json()
