"""Internal gains: normalised inputs (a W sensor must not blow up the model) and their forecast path."""
# ruff: noqa: I001
from __future__ import annotations

from itertools import pairwise

import numpy as np
import pytest

from .core_helpers import SPEC
from .synthetic import simulate
from core.model import HourRecord, OnlineZoneModel, ZoneSpec, gain_path, typical_gains

GSPEC = ZoneSpec(heat_type=SPEC.heat_type, surfaces=SPEC.surfaces, n_gains=1)


def _rec(sim, i: int, gain: float) -> HourRecord:
    return HourRecord(
        temp=float(sim["air"][i]),
        t_out=float(sim["t_out"][i]),
        irr={k: float(v[i]) for k, v in sim["irr"].items()},
        q=float(sim["q"][i]),
        gains=(gain,),
    )


def _train(sim, hours: int) -> OnlineZoneModel:
    m = OnlineZoneModel(GSPEC)
    for i in range(hours):
        m.update(_rec(sim, i, float(sim["gain"][i])), float(sim["air"][i + 1]))
    return m


def _fut(gain: float, hours: int = 72) -> list[HourRecord]:
    return [HourRecord(temp=0.0, t_out=10.0, q=0.0, gains=(gain,)) for _ in range(hours)]


def _gain_col(m: OnlineZoneModel) -> int:
    return m.names.index("gain:0")


def test_first_hour_of_a_watt_gain_does_not_explode():
    """14 days at 0 W, then one hour at 154 W with a big unexplained rise (warm air at the sensor):
    the forecast at a constant 300 W must stay plausible (it went to 120 °C)."""
    sim = simulate(days=15, seed=4)
    m = _train(sim, 14 * 24)
    i = 14 * 24
    m.update(_rec(sim, i, 154.0), float(sim["air"][i]) + 0.5)
    on = m.predict(21.0, _fut(300.0)).mean
    off = m.predict(21.0, _fut(0.0)).mean
    assert max(a - b for a, b in zip(on, off)) < 3.0


def test_gain_in_watts_is_learned():
    """A 0–300 W device switched on and off (true 3e-4 K/h per W) is learned per W."""
    def watts(i: int) -> float:
        return 300.0 if (i // 5) % 3 == 0 else (150.0 if (i // 7) % 4 == 1 else 0.0)

    sim = simulate(days=30, seed=4, gain_w=watts)
    m = _train(sim, 30 * 24 - 1)
    assert m.params()["gain:0"] == pytest.approx(3e-4, rel=0.4)


def test_growing_scale_keeps_predictions():
    sim = simulate(days=10, seed=1, gain_w=lambda i: 200.0 if i % 6 < 3 else 0.0)
    m = _train(sim, 10 * 24 - 1)
    before = m.predict(20.0, _fut(200.0, 24))
    params = m.params()
    m._grow_gain_scale((800.0,))
    after = m.predict(20.0, _fut(200.0, 24))
    assert after.mean == pytest.approx(before.mean, abs=1e-9)
    assert m.params()["gain:0"] == pytest.approx(params["gain:0"])


def test_scale_is_stored():
    sim = simulate(days=5, seed=2, gain_w=lambda i: 250.0 if i % 4 == 0 else 0.0)
    m = _train(sim, 5 * 24 - 1)
    m2 = OnlineZoneModel(GSPEC)
    assert m2.load_dict(m.to_dict())
    assert m2.predict(20.0, _fut(250.0, 12)).mean == pytest.approx(m.predict(20.0, _fut(250.0, 12)).mean)


def test_legacy_gain_parameters_are_reset():
    """Before v0.8.4 gains were learned in raw units (K/h per W) – one hour could make them absurd."""
    sim = simulate(days=5, seed=2)
    m = _train(sim, 5 * 24 - 1)
    d = m.to_dict()
    del d["gain_scale"]
    col = _gain_col(m)
    d["theta"][col] = 0.0147
    d["P"][col][col] = 0.05
    m2 = OnlineZoneModel(GSPEC)
    assert m2.load_dict(d)
    assert m2.params()["gain:0"] == 0.0
    assert m2.P[col, col] == pytest.approx(OnlineZoneModel(GSPEC).P[col, col])
    assert m2.params()["loss"] == pytest.approx(m.params()["loss"])


def test_unseen_gain_level_does_not_blow_up_sigma():
    """Reset gains (scale 1) and a device at 300 W now: σ must not explode (it went to ±20 K)."""
    sim = simulate(days=15, seed=4)
    m = _train(sim, 14 * 24)
    m._reset_gains()
    on = m.predict(21.0, _fut(300.0, 48))
    off = m.predict(21.0, _fut(0.0, 48))
    assert on.mean == pytest.approx(off.mean)
    assert max(a - b for a, b in zip(on.std, off.std)) < 0.5
    q = np.zeros((1, 48))
    _, std_b = m.predict_batch(21.0, _fut(300.0, 48), q)
    assert std_b[0] == pytest.approx(on.std)


def test_prediction_saturates_at_the_largest_seen_gain():
    sim = simulate(days=10, seed=1, gain_w=lambda i: 200.0 if i % 6 < 3 else 0.0)
    m = _train(sim, 10 * 24 - 1)
    assert m.predict(20.0, _fut(1000.0, 12)).mean == pytest.approx(m.predict(20.0, _fut(200.0, 12)).mean)


def test_typical_gains_per_hour_with_fallback():
    samples = [(h, (100.0,)) for h in range(24) if h != 5] + [(8, (300.0,)), (9, ())]
    typ = typical_gains(samples, 1)
    assert typ[8] == (200.0,)
    assert typ[3] == (100.0,)
    assert typ[5] == pytest.approx((2600.0 / 24,))  # no sample at 5 o'clock: mean of all hours
    assert typical_gains([], 1) == {}


def test_gain_path_decays_to_the_typical_value():
    typ = {h: (50.0,) for h in range(24)}
    path = gain_path((300.0,), typ, [h % 24 for h in range(10, 34)])
    assert path[0] == (300.0,)
    assert 50.0 < path[3][0] < 300.0
    assert path[12][0] == pytest.approx(50.0, abs=5.0)
    assert all(a[0] >= b[0] for a, b in pairwise(path))


def test_gain_path_without_history_stays_constant():
    assert gain_path((120.0,), {}, [1, 2, 3]) == [(120.0,)] * 3
    assert gain_path((), {}, [1, 2]) == [(), ()]
