"""Open-Meteo client: outdoor temperature and irradiance per surface orientation.

Two weather models in every request: Open-Meteo's ``best_match`` (ICON-D2/ICON-EU of the DWD in Germany) and
ECMWF IFS. The outdoor temperature is their mean; how far they disagree is the forecast's weather uncertainty
(σ of the outdoor temperature and of the irradiance per surface). The irradiance itself stays ``best_match``
– it is what the zone models learned with.

No Home Assistant imports – pass any aiohttp-compatible ClientSession.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MAIN_MODEL = "best_match"
ALT_MODEL = "ecmwf_ifs025"

# σ of the outdoor temperature: sqrt(floor² + (k·spread)²) – fitted to 14 days of the reference installation
# (Open-Meteo previous runs vs. the outdoor sensor, bias removed: rms ≈ 0.6 K at a small spread, ≈ 1.3 K at 2 K);
# the floor ramps up over the first hours (the current hour is nearly known). The per-horizon σ calibration
# (forecast log) corrects the overall scale.
T_SD_FLOOR = 0.6
T_SD_SPREAD = 0.5
T_SD_RAMP_H = 6.0
IRR_SD_SPREAD = 0.5  # σ of the irradiance = k · |ICON − ECMWF| (clouds: no measurement to fit against)


def compass_to_open_meteo(azimuth: float) -> float:
    """Compass azimuth (0=N, 90=E, 180=S) -> Open-Meteo convention (0=S, -90=E, 90=W)."""
    a = (azimuth - 180.0) % 360.0
    return a - 360.0 if a >= 180.0 else a


@dataclass
class Forecast:
    """Hourly series, index aligned with ``times`` (UTC, hour starts)."""

    times: list[datetime] = field(default_factory=list)
    t_out: list[float] = field(default_factory=list)  # what the planner uses: model mean + sensor correction
    irr: dict[str, list[float]] = field(default_factory=dict)  # key -> W/m²
    fetched_at: datetime | None = None
    t_out_raw: list[float] = field(default_factory=list)  # model mean before the sensor correction
    t_out_spread: list[float] = field(default_factory=list)  # |ICON − ECMWF| (NaN: one model only)
    irr_spread: dict[str, list[float]] = field(default_factory=dict)  # key -> |ICON − ECMWF| W/m²

    def index_of(self, when: datetime) -> int | None:
        hour = when.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        try:
            return self.times.index(hour)
        except ValueError:
            return None

    def irr_at(self, idx: int) -> dict[str, float]:
        return {k: (v[idx] if v[idx] is not None else 0.0) for k, v in self.irr.items()}

    def correct(self, offset: Callable[[datetime], float]) -> None:
        """Outdoor temperature = model mean + ``offset(hour)`` (the learned sensor offset). Idempotent."""
        raw = self.t_out_raw or self.t_out
        self.t_out_raw = list(raw)
        self.t_out = [v + offset(t) for t, v in zip(self.times, raw)]

    def t_out_sd(self, idx: int) -> float:
        """σ of the outdoor temperature in hour ``idx`` (0 for hours before the fetch)."""
        if self.fetched_at is None:
            return 0.0
        lead = (self.times[idx] - self.fetched_at) / timedelta(hours=1)
        if lead < -1.0:
            return 0.0
        floor = T_SD_FLOOR * min(1.0, (max(lead, 0.0) + 1.0) / T_SD_RAMP_H)
        spread = self.t_out_spread[idx] if idx < len(self.t_out_spread) else math.nan
        return math.hypot(floor, T_SD_SPREAD * spread) if math.isfinite(spread) else floor

    def irr_sd_at(self, idx: int) -> dict[str, float]:
        """σ of the irradiance per surface in hour ``idx`` (W/m²)."""
        out: dict[str, float] = {}
        for k, v in self.irr_spread.items():
            s = v[idx] if idx < len(v) else math.nan
            if math.isfinite(s) and s > 0.0:
                out[k] = IRR_SD_SPREAD * s
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "times": [t.isoformat() for t in self.times],
            "t_out": self.t_out,
            "t_out_raw": self.t_out_raw,
            "t_out_spread": self.t_out_spread,
            "irr": self.irr,
            "irr_spread": self.irr_spread,
            "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
        }


def _parse_times(raw: list[str]) -> list[datetime]:
    return [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in raw]


def _series(hourly: dict[str, Any], var: str, model: str) -> list[float | None] | None:
    """One model's series. A single-model answer carries the plain name (``temperature_2m``)."""
    values = hourly.get(f"{var}_{model}")
    if values is None and model == MAIN_MODEL:
        values = hourly.get(var)
    return values


def _num(x: Any) -> float:
    return float(x) if x is not None else math.nan


def _spread(a: list[float | None], b: list[float | None] | None) -> list[float]:
    if b is None:
        return [math.nan] * len(a)
    return [abs(_num(x) - _num(y)) for x, y in zip(a, b)]


async def fetch_forecast(
    session: Any,
    latitude: float,
    longitude: float,
    orientations: Iterable[tuple[str, float, float]],  # (key, tilt, compass azimuth)
    past_days: int = 2,
    forecast_days: int = 3,
) -> Forecast:
    """Fetch temperature + global tilted irradiance for every unique orientation.

    Open-Meteo accepts one tilt/azimuth per request, so we issue one request per orientation – one after the
    other: fired concurrently, the burst was answered with 429 (Too Many Requests) every few hours.
    """
    base = {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "UTC",
        "past_days": past_days,
        "forecast_days": forecast_days,
        "models": f"{MAIN_MODEL},{ALT_MODEL}",
    }
    fc = Forecast(fetched_at=datetime.now(UTC))

    unique: dict[str, tuple[float, float]] = {}
    for key, tilt, azimuth in orientations:
        if key != "0_0":
            unique.setdefault(key, (tilt, azimuth))
    data = await _get(session, {**base, "hourly": "temperature_2m,shortwave_radiation"})
    tilted = [
        await _get(session, {
            **base,
            "hourly": "global_tilted_irradiance",
            "tilt": round(tilt, 1),
            "azimuth": round(compass_to_open_meteo(azimuth), 1),
        })
        for tilt, azimuth in unique.values()
    ]

    hourly = data["hourly"]
    fc.times = _parse_times(hourly["time"])
    main = _series(hourly, "temperature_2m", MAIN_MODEL) or []
    alt = _series(hourly, "temperature_2m", ALT_MODEL)
    blend = []
    for i, x in enumerate(main):
        a, b = _num(x), _num(alt[i]) if alt is not None and i < len(alt) else math.nan
        blend.append(a if not math.isfinite(b) else (b if not math.isfinite(a) else 0.5 * (a + b)))
    fc.t_out_raw = blend
    fc.t_out = list(blend)
    fc.t_out_spread = _spread(main, alt)
    # irradiance (+ horizontal irradiance as fallback key "0_0"): best_match, the ECMWF spread as uncertainty
    surfaces = [("0_0", hourly, "shortwave_radiation")]
    surfaces += [(key, d["hourly"], "global_tilted_irradiance") for key, d in zip(unique, tilted)]
    for key, h, var in surfaces:
        values = _series(h, var, MAIN_MODEL) or []
        fc.irr[key] = [float(x or 0.0) for x in values]
        if key != "0_0":
            fc.irr_spread[key] = _spread(values, _series(h, var, ALT_MODEL))
    return fc


RETRY_DELAYS: tuple[float, ...] = (5.0, 20.0)  # waits before retrying a 429 (Too Many Requests)


async def _get(session: Any, params: dict[str, Any]) -> dict[str, Any]:
    for delay in (*RETRY_DELAYS, None):
        async with session.get(OPEN_METEO_URL, params=params, timeout=30) as resp:
            if resp.status != 429 or delay is None:
                resp.raise_for_status()
                return await resp.json()
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")
