"""Shared helpers for HA-free core tests (same import style as test_core.py)."""
from __future__ import annotations

import os
import sys
from functools import lru_cache

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "thermocast"))

from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec

from .synthetic import simulate

SPEC = ZoneSpec(
    heat_type="fbh",
    surfaces=(
        SurfaceSpec(kind="window", azimuth=90, tilt=90, name="Fenster Ost"),
        SurfaceSpec(kind="roof", azimuth=180, tilt=40, name="Schräge Süd"),
    ),
)


def record(sim, i: int) -> HourRecord:
    return HourRecord(
        temp=float(sim["air"][i]),
        t_out=float(sim["t_out"][i]),
        irr={k: float(v[i]) for k, v in sim["irr"].items()},
        q=float(sim["q"][i]),
    )


@lru_cache
def _trained_state(seed: int, days: int) -> dict:
    sim = simulate(days=days, seed=seed)
    m = OnlineZoneModel(SPEC)
    for i in range(days * 24 - 1):
        m.update(record(sim, i), float(sim["air"][i + 1]))
    return m.to_dict()


def trained_model(seed: int = 4, days: int = 30) -> OnlineZoneModel:
    m = OnlineZoneModel(SPEC)
    assert m.load_dict(_trained_state(seed, days))
    return m


def future(t_out: float, hours: int, irr: dict[str, float] | None = None) -> list[HourRecord]:
    return [HourRecord(temp=0.0, t_out=t_out, irr=dict(irr or {}), q=0.0) for _ in range(hours)]
