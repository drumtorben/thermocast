"""The planner's batched prediction equals predict() for every heating schedule."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from .core_helpers import future, trained_model
from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec
from core.planner import Candidate, ZonePlanInput, plan


def _schedules(n_hours: int) -> np.ndarray:
    rng = np.random.default_rng(1)
    q = np.zeros((5, n_hours))
    q[1, 2:6] = 12.0
    q[2, 0:3] = 8.0
    q[3] = rng.uniform(0, 15, n_hours)
    q[4, n_hours - 2:] = 20.0
    return q


def _assert_same(m: OnlineZoneModel, fut: list[HourRecord], **kw) -> None:
    q = _schedules(len(fut))
    mean, std = m.predict_batch(20.3, fut, q, **kw)
    for row in range(q.shape[0]):
        ref = m.predict(20.3, [replace(r, q=float(x)) for r, x in zip(fut, q[row])], **kw)
        assert mean[row] == pytest.approx(ref.mean, abs=1e-12)
        assert std[row] == pytest.approx(ref.std, abs=1e-12)


def test_batch_equals_predict_trained():
    m = trained_model()
    _assert_same(m, future(5.0, 30, irr={"90_90": 300.0, "40_180": 500.0}))
    _assert_same(m, future(-3.0, 12), var0=0.2)
    _assert_same(m, future(5.0, 12), history=[replace(r, q=20.0) for r in m.history])


@pytest.mark.parametrize("n_hist", [0, 1, 3])
def test_batch_equals_predict_with_short_history(n_hist: int):
    """A fresh model has fewer past records than its longest lag (oldest-record fallback)."""
    spec = ZoneSpec(
        heat_type="fbh", surfaces=(SurfaceSpec("roof", 180, 40),), n_neighbors=2, n_gains=1,
    )
    m = OnlineZoneModel(spec)
    m.theta = np.abs(np.random.default_rng(2).normal(0.01, 0.005, m.dim))
    hist = [HourRecord(temp=20.0, t_out=4.0, irr={"40_180": 100.0 * i}, q=5.0 * i) for i in range(n_hist)]
    fut = [
        HourRecord(temp=0.0, t_out=3.0 + i, irr={"40_180": 50.0 * i},
                   neighbors=(19.0,) if i % 2 else (19.0, 21.5), gains=(0.3,) if i % 3 else ())
        for i in range(10)
    ]
    _assert_same(m, fut, history=hist)


def test_heating_batch_matches_single_candidates_with_cap():
    m = trained_model()
    fut = future(8.0, 24)
    zone = ZonePlanInput(
        name="z", model=m, temp_now=20.6, future=fut, comfort_low=[20.0] * 24, q_on=25.0,
        charge_cap=[21.0] * 24, std_scale=(1.0, 1.5, 2.0),
    )
    cands = [Candidate(None), Candidate(0, 6), Candidate(3, 12), Candidate(10, 4)]
    q, mean, std = zone.heating_batch(cands)
    for k, cand in enumerate(cands):
        recs, pred = zone.heating_prediction(cand)
        assert [r.q for r in recs] == q[k].tolist()
        assert mean[k].tolist() == pytest.approx(pred.mean, abs=1e-12)
        assert std[k].tolist() == pytest.approx(pred.std, abs=1e-12)
    assert (q[2] == 0).any() and (q[2] > 0).any()  # the cap closed the valve during the long block


def test_plan_with_empty_horizon():
    zone = ZonePlanInput(name="z", model=trained_model(), temp_now=20.0, future=[], comfort_low=[])
    assert plan([zone]).best.start is None
