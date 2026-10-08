"""Open-Meteo fetch: one request after the other, a 429 (Too Many Requests) is waited out and retried."""
# ruff: noqa: I001
from __future__ import annotations

import asyncio
from typing import Self

import pytest

from . import core_helpers  # noqa: F401  (sets sys.path for `core`)
from core import forecast


class _Resp:
    def __init__(self, status: int, payload: dict) -> None:
        self.status, self._payload = status, payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self) -> dict:
        return self._payload

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc) -> None:
        return None


class _Session:
    """Answers 429 for the first ``busy`` calls; records how many requests ran at the same time."""

    def __init__(self, busy: int = 0) -> None:
        self.busy, self.calls, self.open, self.max_open = busy, 0, 0, 0

    def get(self, url: str, params: dict, timeout: int):
        self.calls += 1
        if self.calls <= self.busy:
            return _Resp(429, {})
        hourly = {"time": ["2026-10-08T10:00", "2026-10-08T11:00"]}
        if "tilt" in params:
            hourly["global_tilted_irradiance"] = [100.0, 200.0]
        else:
            hourly |= {"temperature_2m": [12.0, 13.0], "shortwave_radiation": [50.0, 60.0]}
        return _Resp(200, {"hourly": hourly})


def _fetch(session: _Session):
    orientations = [("90_90", 90.0, 90.0), ("45_180", 45.0, 180.0)]
    return asyncio.run(forecast.fetch_forecast(session, 53.2, 9.5, orientations))


def test_a_busy_open_meteo_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    waits: list[float] = []

    async def no_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(forecast.asyncio, "sleep", no_sleep)
    session = _Session(busy=2)
    fc = _fetch(session)
    assert waits == list(forecast.RETRY_DELAYS)  # waited, then the third try went through
    assert fc.t_out == [12.0, 13.0] and fc.irr["90_90"] == [100.0, 200.0] and set(fc.irr) == {"0_0", "90_90", "45_180"}
    assert session.calls == 2 + 3  # two refusals, then temperature + two orientations


def test_still_busy_after_the_retries_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(seconds: float) -> None:
        return None

    monkeypatch.setattr(forecast.asyncio, "sleep", no_sleep)
    with pytest.raises(RuntimeError, match="429"):
        _fetch(_Session(busy=10))  # the coordinator keeps the old forecast (fail-safe after 2 h)
