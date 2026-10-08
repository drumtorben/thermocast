"""Warm start: learn a zone model from past hourly data instead of starting from the prior (no HA imports).

Inputs are hourly means (recorder long-term statistics) – the change between two consecutive hourly
means approximates the hourly temperature change. That is slightly smoothed compared to the live
samples (start-of-hour values), which the forgetting factor washes out within days.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, tzinfo
from typing import Any

from .model import HourRecord, OnlineZoneModel, update_q_on
from .quality import param_snapshot


@dataclass
class WarmstartInputs:
    """Hourly series aligned to ``hours`` (UTC hour starts, consecutive). None = no data."""

    hours: list[datetime]
    temp: list[float | None]  # zone temperature (mean over its sensors)
    t_out: list[float | None]
    irr: dict[str, list[float | None]] = field(default_factory=dict)  # per surface key, W/m²
    flow: list[float | None] = field(default_factory=list)
    heating: list[float | None] | None = None  # share of the hour the heating pump ran; None = not configured
    valve: list[float | None] | None = None  # radiator valve opening %, None = not configured
    neighbors: list[list[float | None]] = field(default_factory=list)
    gains: list[list[float | None]] = field(default_factory=list)
    window: list[list[float | None]] = field(default_factory=list)  # share of the hour a window was open


def _at(series: list | None, i: int):
    return series[i] if series is not None and i < len(series) else None


def build_records(inp: WarmstartInputs, surface_keys: list[str]) -> list[tuple[datetime, HourRecord, float]]:
    """(hour start, inputs of that hour, temperature one hour later) – the same records the live loop learns."""
    out: list[tuple[datetime, HourRecord, float]] = []
    for i in range(len(inp.hours) - 1):
        temp, nxt = inp.temp[i], inp.temp[i + 1]
        t_out = inp.t_out[i]
        irr = {k: v for k in surface_keys if (v := _at(inp.irr.get(k), i)) is not None}
        flow = _at(inp.flow, i)
        q = 0.0
        if temp is not None and flow is not None:
            # live loop: heating = pump running, or (no pump entity) "flow available" -> on
            share = 1.0 if inp.heating is None else (_at(inp.heating, i) or 0.0)
            q = share * max(0.0, flow - temp)
            if inp.valve is not None:
                valve = _at(inp.valve, i)
                q *= (valve / 100.0) if valve is not None else 0.0
        neighbors = tuple(v if (v := _at(n, i)) is not None else temp for n in inp.neighbors)
        gains = tuple(v if (v := _at(g, i)) is not None else 0.0 for g in inp.gains)
        # aired in this hour, in the next (its mean is the end temperature) or the last (the air still recovers)
        window_open = any((_at(w, j) or 0.0) > 0 for w in inp.window for j in (i - 1, i, i + 1) if j >= 0)
        valid = (
            temp is not None
            and nxt is not None
            and t_out is not None
            and not window_open
            and len(irr) == len(surface_keys)
            and all(n is not None for n in neighbors)
        )
        rec = HourRecord(
            temp=temp if temp is not None else math.nan,
            t_out=t_out if t_out is not None else math.nan,
            irr=irr,
            q=q,
            neighbors=tuple(n if n is not None else math.nan for n in neighbors),
            gains=gains,
            valid=valid,
        )
        out.append((inp.hours[i], rec, nxt if nxt is not None else math.nan))
    return out


@dataclass
class WarmstartResult:
    model: OnlineZoneModel
    log: list[dict[str, Any]]  # hour-log entries (same format as the live log)
    params: list[dict[str, Any]]  # daily parameter snapshots
    q_on: float | None  # typical heating proxy during blocks
    learned: int


def warm_train(
    model: OnlineZoneModel,
    records: list[tuple[datetime, HourRecord, float]],
    tz: tzinfo,
    log_hours: int = 14 * 24,
    param_days: int = 30,
    pump_share: list[float | None] | None = None,
) -> WarmstartResult:
    """Run the RLS over the records (in place on ``model``) and rebuild log + parameter history.

    ``pump_share`` (aligned to ``records``, share of the hour the heating pump ran; None = not configured)
    decides which hours count as block hours for ``q_on`` – the same rule as the live loop, in time order, so
    the most recent blocks dominate."""
    log: list[dict[str, Any]] = []
    params: list[dict[str, Any]] = []
    q_on: float | None = None
    for i, (t, rec, nxt) in enumerate(records):
        err = model.update(rec, nxt)
        share = None if pump_share is None else ((pump_share[i] if i < len(pump_share) else None) or 0.0)
        q_on = update_q_on(q_on, rec.q, share)
        rec_dict = rec.to_dict()
        rec_dict["temp"] = rec.temp if math.isfinite(rec.temp) else None
        rec_dict["t_out"] = rec.t_out if math.isfinite(rec.t_out) else None
        log.append(
            {"t": t.isoformat(), "rec": rec_dict, "temp_next": nxt if math.isfinite(nxt) else None, "err": err}
        )
        day = t.astimezone(tz).date().isoformat()
        if not params or params[-1]["date"] != day:
            params.append({"date": day, **param_snapshot(model)})
    return WarmstartResult(
        model=model,
        log=log[-log_hours:],
        params=params[-param_days:],
        q_on=q_on,
        learned=model.n_updates,
    )
