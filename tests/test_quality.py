"""Model quality: hour log, hindcast, forecast metrics, parameter interpretation."""
# ruff: noqa: I001
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from .core_helpers import SPEC, record, trained_model
from .synthetic import simulate
from core.model import OnlineZoneModel, Prediction
from core.quality import (
    ForecastLog,
    RingLog,
    forecast_metrics,
    heat_lag_profile,
    hindcast,
    hindcast_metrics,
    interpret,
    measured_by_hour,
    one_step_metrics,
    param_snapshot,
)

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 1, 22, tzinfo=UTC)  # 02.10. 00:00 local


def _entries(sim, start: int, hours: int, t0: datetime = T0) -> list[dict]:
    out = []
    for i in range(hours):
        rec = record(sim, start + i)
        out.append({"t": (t0 + timedelta(hours=i)).isoformat(), "rec": rec.to_dict(),
                    "temp_next": float(sim["air"][start + i + 1]), "err": 0.01 * (-1) ** i})
    return out


def test_ring_log():
    log = RingLog(maxlen=3)
    for i in range(5):
        log.append({"i": i})
    assert [e["i"] for e in log.to_list()] == [2, 3, 4] and len(log) == 3
    other = RingLog(maxlen=2)
    other.load(log.to_list())
    assert [e["i"] for e in other.to_list()] == [3, 4]


def test_forecast_log_roundtrip_and_prune():
    flog = ForecastLog(max_issues=2)
    pred = Prediction(mean=[20.0 + 0.1 * k for k in range(24)], std=[0.05 * (k + 1) for k in range(24)])
    for h in range(3):
        flog.record(T0 + timedelta(hours=h), pred)
    assert len(flog.to_dict()) == 2
    assert flog.predicted(T0 + timedelta(hours=2 + 6), 6) == (pytest.approx(20.5), pytest.approx(0.3))
    assert flog.predicted(T0 + timedelta(hours=6), 6) is None  # pruned issue
    restored = ForecastLog(max_issues=2)
    restored.load(flog.to_dict())
    assert restored.to_dict() == flog.to_dict()


def test_hindcast_restarts_at_midnight_and_gaps():
    sim = simulate(days=35, seed=4)
    m = trained_model()
    entries = _entries(sim, 30 * 24, 48)
    del entries[30]  # a gap (HA restart)
    hc = hindcast(m, entries, TZ)
    assert len(hc) == len(entries)
    # restart points use the measured temperature as start: first step error is a one-step error
    for i in (0, 24, 30):
        rec_temp = entries[i]["rec"]["temp"]
        assert abs(hc[i]["start"] - rec_temp) < 1e-9
    assert all(set(h["contrib"]) >= {"loss", "heat"} for h in hc if h)
    metrics = hindcast_metrics(entries, hc, since=None)
    assert metrics["n"] == len(entries) and metrics["mae"] < 0.6


def test_hindcast_skips_unusable_hours():
    sim = simulate(days=35, seed=4)
    entries = _entries(sim, 30 * 24, 5)
    entries[2]["rec"]["t_out"] = float("nan")
    entries[3]["rec"]["valid"] = False
    hc = hindcast(trained_model(), entries, TZ)
    assert hc[2] is None and hc[3] is not None and hc[4]["start"] == hc[3]["mean"]
    assert hindcast_metrics(entries, hc, since=None)["n"] == 3  # nan + invalid hour excluded


def test_one_step_metrics():
    sim = simulate(days=35, seed=4)
    entries = _entries(sim, 30 * 24, 10)
    entries[0]["err"] = None
    m = one_step_metrics(entries, since=T0 + timedelta(hours=1))
    assert m["n"] == 9 and m["mae"] == pytest.approx(0.01) and abs(m["bias"]) <= 0.01


def test_forecast_metrics_by_horizon():
    flog = ForecastLog()
    pred = Prediction(mean=[20.0] * 24, std=[0.5] * 24)
    flog.record(T0, pred)
    measured = {T0 + timedelta(hours=1): 20.2, T0 + timedelta(hours=6): 21.0, T0 + timedelta(hours=24): 19.8}
    fm = forecast_metrics(flog, measured, since=None)
    assert fm["1"] == {"n": 1, "mae": pytest.approx(0.2), "bias": pytest.approx(-0.2), "coverage": 1.0}
    assert fm["6"]["coverage"] == 0.0 and fm["24"]["bias"] == pytest.approx(0.2)
    assert fm["3"]["n"] == 0 and fm["3"]["mae"] is None


def test_measured_by_hour():
    sim = simulate(days=35, seed=4)
    entries = _entries(sim, 30 * 24, 3)
    mb = measured_by_hour(entries)
    assert mb[T0] == entries[0]["rec"]["temp"]
    assert mb[T0 + timedelta(hours=3)] == entries[2]["temp_next"]


def test_interpret_and_lags():
    m = trained_model()
    params = {p["key"]: p for p in interpret(m, q_on=15.0)}
    loss = m.params()["loss"]
    assert params["tau"]["value"] == pytest.approx(1 / loss) and params["tau"]["unit"] == "h"
    assert params["sun:90_90:window"]["label"] == "Fenster Ost"
    assert params["heat"]["value"] == pytest.approx(15.0 * sum(v for k, v in m.params().items() if k.startswith("heat:")))
    assert all(p["std"] is None or p["std"] >= 0 for p in params.values())
    lags = heat_lag_profile(m)
    assert [lg["lag"] for lg in lags] == list(SPEC.heat_lags)
    snap = param_snapshot(m)
    assert set(snap["theta"]) == set(m.names) and all(math.isfinite(v) for v in snap["std"].values())


def test_interpret_prior_model_without_loss():
    m = OnlineZoneModel(SPEC)
    m.theta[m.names.index("loss")] = 0.0
    tau = next(p for p in interpret(m, 10.0) if p["key"] == "tau")
    assert tau["value"] is None


def test_calibration_widens_sigma_to_cover_68_percent():
    import random

    from core.quality import calibration, std_scale_profile

    rng = random.Random(1)
    flog = ForecastLog()
    measured = {}
    for h in range(200):
        issue = T0 + timedelta(hours=h)
        flog.record(issue, Prediction(mean=[20.0] * 24, std=[0.1] * 24))
        measured[issue + timedelta(hours=6)] = 20.0 + rng.gauss(0, 0.2)  # true spread: 2 σ
    f = calibration(flog, measured, since=None)
    assert 1.6 < f[6] < 2.4
    # every target hour is checked for every horizon (each issue predicts all horizons)
    assert f[1] > 1.0
    few = calibration(flog, dict(list(measured.items())[:10]), since=None)
    assert few == {k: 1.0 for k in (1, 3, 6, 12, 24)}  # not enough checks -> unchanged
    assert std_scale_profile(few) == ()
    prof = std_scale_profile({1: 1.0, 3: 1.4, 6: 2.0, 12: 2.0, 24: 3.0})
    assert prof[0] == 1.0 and prof[1] == pytest.approx(1.2) and prof[2] == 1.4 and prof[23] == 3.0 and prof[47] == 3.0


def test_calibration_never_narrows():
    from core.quality import calibration

    flog = ForecastLog()
    measured = {}
    for h in range(100):
        issue = T0 + timedelta(hours=h)
        flog.record(issue, Prediction(mean=[20.0] * 24, std=[1.0] * 24))
        measured[issue + timedelta(hours=1)] = 20.01
    assert calibration(flog, measured, since=None)[1] == 1.0


def test_solar_lag_profiles():
    from core.quality import solar_lag_profiles

    m = trained_model()
    profiles = {p["key"]: p for p in solar_lag_profiles(m)}
    assert [lg["lag"] for lg in profiles["sun:40_180:roof"]["lags"]] == [1, 2, 3, 4, 6]
    assert [lg["lag"] for lg in profiles["sun:90_90:window"]["lags"]] == [0, 1]
    assert profiles["sun:40_180:roof"]["label"] == "Schräge Süd"
    total = sum(lg["value"] for lg in profiles["sun:40_180:roof"]["lags"])
    assert total == pytest.approx(m.solar_response()["roof:40_180"])
