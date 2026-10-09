"""Weather: outdoor sensor offset, two-model forecast spread and the weather share of the forecast σ."""
# ruff: noqa: I001
from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from .core_helpers import SPEC, future, record, trained_model
from .synthetic import simulate
from core.forecast import IRR_SD_SPREAD, T_SD_FLOOR, T_SD_RAMP_H, T_SD_SPREAD, Forecast
from core.model import OnlineZoneModel
from core.planner import ZonePlanInput
from core.rollout import ActuatorState, rollout
from core.rules import Rules
from core.weather import BIAS_ALPHA, BIAS_MIN_WEIGHT, OutdoorBias, weather_summary

T0 = datetime(2026, 10, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)


def _offset(h: int) -> float:
    """Sensor − forecast like at the reference installation: ~1.3 K, more at noon."""
    return 1.3 + 0.6 * math.cos(2 * math.pi * (h - 12) / 24)


# ------------------------------------------------------------------ offset
def test_offset_is_learned_per_hour_of_day():
    bias = OutdoorBias()
    hours = [T0 + i * HOUR for i in range(14 * 24)]
    rng = np.random.default_rng(0)
    n = bias.learn(hours, [5.0 + _offset(t.hour) + rng.normal(0, 0.3) for t in hours], [5.0] * len(hours))
    assert n == len(hours)
    prof = bias.profile()
    for h in range(24):
        assert prof[h] == pytest.approx(_offset(h), abs=0.2)
    assert bias.offset(T0 + 12 * HOUR) > bias.offset(T0) + 0.8  # noon reads warmer than midnight
    assert bias.days > 10


def test_few_samples_are_shrunk_and_outliers_ignored():
    bias = OutdoorBias()
    assert bias.empty and bias.offset(T0) == 0.0
    assert bias.update(T0, 7.0, 5.0)
    assert bias.offset(T0) == pytest.approx(2.0 * BIAS_ALPHA / BIAS_MIN_WEIGHT)  # one day: shrunk towards 0
    assert bias.offset(T0 + HOUR) == pytest.approx(bias.offset(T0))  # unknown neighbours borrow from known hours
    assert not bias.update(T0 + HOUR, 30.0, 5.0)  # a broken sensor is no offset
    assert not bias.update(T0 + HOUR, None, 5.0) and not bias.update(T0 + HOUR, 5.0, math.nan)
    assert bias.weight[1] == 0.0


def test_offset_roundtrip():
    bias = OutdoorBias()
    bias.learn([T0 + i * HOUR for i in range(48)], [6.0] * 48, [5.0] * 48)
    again = OutdoorBias.from_dict(bias.to_dict())
    assert again.profile() == bias.profile()
    assert OutdoorBias.from_dict({"num": [1.0]}).empty and OutdoorBias.from_dict(None).empty


def test_correction_is_idempotent():
    fc = Forecast(times=[T0, T0 + HOUR], t_out=[5.0, 6.0], fetched_at=T0)
    fc.correct(lambda t: 1.0)
    fc.correct(lambda t: 1.5)
    assert fc.t_out == [6.5, 7.5] and fc.t_out_raw == [5.0, 6.0]


def test_sensor_offset_on_the_synthetic_house():
    """The model learned with a sensor 1–2 K above the forecast: fed the raw forecast it predicts the room too cold;
    with the learned offset the 24-h prediction is as good as with a sensor that matches the forecast."""
    days = 30
    sim = simulate(days=days, seed=3)
    n = days * 24
    hours = [T0 + i * HOUR for i in range(n)]
    true = [float(sim["t_out"][i]) for i in range(n)]
    sensor = [true[i] + _offset(hours[i].hour) for i in range(n)]
    learn_until = 22 * 24

    def trained(t_out: list[float]) -> OnlineZoneModel:
        model = OnlineZoneModel(SPEC)
        for i in range(learn_until):
            model.update(replace(record(sim, i), t_out=t_out[i]), float(sim["air"][i + 1]))
        return model

    bias = OutdoorBias()
    bias.learn(hours[:learn_until], sensor, true)

    def bias_24h(model: OnlineZoneModel, measured: list[float], offset) -> float:
        errs = []
        for start in range(learn_until, n - 25, 6):
            hist = [replace(record(sim, i), t_out=measured[i]) for i in range(start - 6, start)]
            fut = [replace(record(sim, i), t_out=true[i] + offset(hours[i])) for i in range(start, start + 24)]
            p = model.predict(float(sim["air"][start]), fut, history=hist, with_contrib=False)
            errs.append(p.mean[-1] - float(sim["air"][start + 24]))
        return float(np.mean(errs))

    with_sensor = trained(sensor)
    reference = bias_24h(trained(true), true, lambda t: 0.0)  # the model's own error
    raw = bias_24h(with_sensor, sensor, lambda t: 0.0)
    fixed = bias_24h(with_sensor, sensor, bias.offset)
    assert raw - reference < -0.1  # the raw forecast is too cold for this model
    assert abs(fixed - reference) < abs(raw - reference) / 4


def test_weather_summary_for_the_model_tab():
    from zoneinfo import ZoneInfo

    bias = OutdoorBias()
    hours = [T0 + i * HOUR for i in range(10 * 24)]
    bias.learn(hours, [5.0 + (2.0 if t.hour == 12 else 1.0) for t in hours], [5.0] * len(hours))
    now = T0 + 10 * 24 * HOUR
    times = [now + h * HOUR for h in range(-2, 30)]
    fc = Forecast(times=times, t_out=[5.0] * 32, fetched_at=now, t_out_spread=[1.0] * 31 + [9.0])
    w = weather_summary(bias, fc, now, ZoneInfo("Europe/Berlin"))
    assert w["offset_by_hour"][14] == max(w["offset_by_hour"])  # 12 UTC = 14 local (summer time)
    assert w["offset_mean"] == pytest.approx(1.0 + 1 / 24, abs=0.03) and w["days"] > 7
    assert w["spread_mean"] == 1.0 and w["spread_max"] == 1.0  # only the next 24 h count
    assert 0 < w["sd_mean"] < math.hypot(T_SD_FLOOR, T_SD_SPREAD)
    empty = weather_summary(OutdoorBias(), None, now, UTC)
    assert empty["offset_by_hour"] is None and empty["spread_mean"] is None


# ---------------------------------------------------------- forecast spread
def test_outdoor_sd_grows_with_spread_and_lead():
    times = [T0 + h * HOUR for h in range(30)]
    fc = Forecast(times=times, t_out=[5.0] * 30, fetched_at=T0, t_out_spread=[0.0] * 29 + [2.0])
    assert fc.t_out_sd(0) == pytest.approx(T_SD_FLOOR / T_SD_RAMP_H)  # the current hour is nearly known
    assert fc.t_out_sd(10) == pytest.approx(T_SD_FLOOR)
    assert fc.t_out_sd(29) == pytest.approx(math.hypot(T_SD_FLOOR, 2.0 * T_SD_SPREAD))
    fc.t_out_spread[10] = math.nan  # one model only
    assert fc.t_out_sd(10) == pytest.approx(T_SD_FLOOR)
    assert Forecast(times=times, t_out=[5.0] * 30, fetched_at=T0 + 5 * HOUR).t_out_sd(0) == 0.0  # past


def test_irradiance_sd_from_spread():
    fc = Forecast(times=[T0], irr={"90_90": [300.0]}, irr_spread={"90_90": [200.0], "40_180": [math.nan]})
    assert fc.irr_sd_at(0) == {"90_90": pytest.approx(IRR_SD_SPREAD * 200.0)}


# ------------------------------------------------------------ weather σ share
def _uncertain(fut, t_sd=0.0, irr_sd=None):
    return [replace(r, t_out_sd=t_sd, irr_sd=dict(irr_sd or {})) for r in fut]


def test_outdoor_uncertainty_adds_up_with_the_loss_rate():
    m = trained_model()
    fut = future(5.0, 24)
    base = m.predict(20.5, fut)
    pred = m.predict(20.5, _uncertain(fut, 1.0))
    a = float(m.theta[1])
    decay = 1.0 - a
    expect = [a * sum(decay**k for k in range(h + 1)) for h in range(24)]
    assert pred.weather == pytest.approx(expect, rel=1e-9)
    assert pred.mean == base.mean  # the mean does not change
    assert pred.std == pytest.approx([math.sqrt(s * s + w * w) for s, w in zip(base.std, expect)], rel=1e-9)
    assert base.weather == [0.0] * 24


def test_irradiance_uncertainty_follows_the_sun_lags():
    m = trained_model()
    fut = future(5.0, 12, irr={"40_180": 400.0})
    sun = _uncertain(fut, irr_sd={"40_180": 150.0})
    pred = m.predict(20.5, sun)
    assert pred.weather[-1] > 0.0
    roof = [(lag, col) for key, lag, col in m._solar_cols if key == "40_180"]
    assert pred.weather[0] == pytest.approx(sum(float(m.theta[c]) * 150.0 for lag, c in roof if lag == 0))


def test_batch_equals_predict_with_weather_sd():
    m = trained_model()
    fut = _uncertain(future(5.0, 20, irr={"90_90": 300.0, "40_180": 500.0}), 0.8, {"90_90": 80.0, "40_180": 120.0})
    q = np.zeros((3, 20))
    q[1, 2:8] = 12.0
    q[2, 10:] = 20.0
    mean, std = m.predict_batch(20.3, fut, q, var0=0.01, w0=0.05)
    for row in range(3):
        ref = m.predict(20.3, [replace(r, q=float(x)) for r, x in zip(fut, q[row])], var0=0.01, w0=0.05)
        assert mean[row] == pytest.approx(ref.mean, abs=1e-12)
        assert std[row] == pytest.approx(ref.std, abs=1e-12)


def test_rollout_carries_the_weather_share_like_one_prediction():
    """Hour by hour (rollout) the weather share adds up linearly, as in one prediction over the whole horizon."""
    m = trained_model()
    fut = _uncertain(future(22.0, 30), 1.0)  # warm: no block
    zone = ZonePlanInput(name="z", model=m, temp_now=21.5, future=fut, comfort_low=[None] * 30)
    ro = rollout([zone], 6, Rules(min_block_h=2, min_pause_h=2, max_switches=12), ActuatorState(on=False))
    assert not any(ro.on)
    ref = m.predict(21.5, fut[:6])
    assert ro.zones["z"].std == pytest.approx(ref.std, rel=1e-9)
