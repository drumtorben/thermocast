"""Tests for the HA-independent core (run with: pytest tests/test_core.py)."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "custom_components", "thermocast"))
sys.path.insert(0, os.path.dirname(__file__))

from core.forecast import compass_to_open_meteo
from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec
from core.planner import ZonePlanInput, plan
from synthetic import simulate

SPEC = ZoneSpec(
    heat_type="fbh",
    surfaces=(
        SurfaceSpec(kind="window", azimuth=90, tilt=90, name="Fenster Ost"),
        SurfaceSpec(kind="roof", azimuth=180, tilt=40, name="Schräge Süd"),
    ),
)


def _records(sim, i):
    return HourRecord(
        temp=float(sim["air"][i]),
        t_out=float(sim["t_out"][i]),
        irr={k: float(v[i]) for k, v in sim["irr"].items()},
        q=float(sim["q"][i]),
    )


def _train(sim, hours):
    m = OnlineZoneModel(SPEC)
    for i in range(hours):
        m.update(_records(sim, i), float(sim["air"][i + 1]))
    return m


def test_compass_conversion():
    assert compass_to_open_meteo(180) == 0
    assert compass_to_open_meteo(90) == -90
    assert compass_to_open_meteo(270) == 90
    assert compass_to_open_meteo(0) == -180


def test_surface_from_dict_validates():
    s = SurfaceSpec.from_dict({"kind": "roof", "azimuth": 180, "tilt": 40})
    assert s.key == "40_180"
    with pytest.raises(ValueError):
        SurfaceSpec.from_dict({"kind": "door", "azimuth": 0})


def test_online_learning_converges_and_forecasts():
    sim = simulate(days=40, seed=1)
    train_h = 30 * 24
    m = _train(sim, train_h)
    assert m.mae < 0.08, m.mae  # one-step error in K/h

    # 24 h free-run forecast on unseen days, using true inputs
    errs = []
    for start in range(train_h, train_h + 9 * 24, 24):
        future = [_records(sim, i) for i in range(start, start + 24)]
        pred = m.predict(float(sim["air"][start]), future)
        truth = sim["air"][start + 1 : start + 25]
        errs.append(np.mean(np.abs(np.asarray(pred.mean) - truth)))
        m2 = m  # keep learning online while evaluating
        for i in range(start, start + 24):
            m2.update(_records(sim, i), float(sim["air"][i + 1]))
    assert np.mean(errs) < 0.6, errs


def test_learns_both_solar_paths():
    sim = simulate(days=40, seed=2)
    m = _train(sim, 40 * 24 - 1)
    solar = m.solar_response()
    assert solar["window:90_90"] > 0.05
    assert solar["roof:40_180"] > 0.05


def test_persistence_roundtrip():
    sim = simulate(days=5, seed=3)
    m = _train(sim, 100)
    m2 = OnlineZoneModel(SPEC)
    assert m2.load_dict(m.to_dict())
    assert np.allclose(m.theta, m2.theta)
    other = OnlineZoneModel(ZoneSpec(heat_type="radiator"))
    assert not other.load_dict(m.to_dict())  # layout changed -> rejected


def test_planner_heats_when_cold_and_not_when_warm():
    sim = simulate(days=30, seed=4)
    m = _train(sim, 29 * 24)

    def zone(t_out, temp_now):
        future = [HourRecord(temp=0, t_out=t_out, irr={}, q=0.0) for _ in range(24)]
        return ZonePlanInput(
            name="z", model=m, temp_now=temp_now, future=future,
            comfort_low=[20.0] * 24, q_on=15.0,
        )

    cold = plan([zone(-5.0, 20.3)])
    assert cold.best.start is not None
    warm = plan([zone(18.0, 21.5)])
    assert warm.best.start is None


def test_slow_roof_needs_long_lags(monkeypatch):
    """A roof that passes the sun on with ~7 h delay (attic): lags up to 6 h simulate it better."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    import core.model as cm
    from core.quality import hindcast, hindcast_metrics

    sim = simulate(days=40, seed=3, roof_delay_h=7.0)
    t0 = datetime(2026, 9, 1, 22, tzinfo=UTC)

    def hindcast_mae(lags):
        monkeypatch.setitem(cm.SURFACE_LAGS, "roof", lags)
        m = OnlineZoneModel(SPEC)
        for i in range(30 * 24):
            m.update(_records(sim, i), float(sim["air"][i + 1]))
        entries = [
            {"t": (t0 + timedelta(hours=i)).isoformat(), "rec": _records(sim, i).to_dict(),
             "temp_next": float(sim["air"][i + 1]), "err": None}
            for i in range(30 * 24, 40 * 24 - 1)
        ]
        return hindcast_metrics(entries, hindcast(m, entries, ZoneInfo("Europe/Berlin")), None)["mae"]

    assert hindcast_mae((1, 2, 3, 4, 6)) < hindcast_mae((1, 2, 3))
