"""Contribution decomposition, start variance and external history of predict()."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace

import pytest

from .core_helpers import future, trained_model
from core.model import OnlineZoneModel, SurfaceSpec, ZoneSpec


def test_contributions_sum_to_temperature_change():
    m = trained_model()
    fut = future(5.0, 24, irr={"90_90": 300.0, "40_180": 500.0})
    fut = [replace(r, q=12.0 if 3 <= i < 7 else 0.0) for i, r in enumerate(fut)]
    pred = m.predict(20.5, fut)
    prev = 20.5
    assert len(pred.contrib) == 24
    for mean, contrib in zip(pred.mean, pred.contrib):
        assert sum(contrib.values()) == pytest.approx(mean - prev, abs=1e-9)
        prev = mean


def test_contribution_groups():
    m = trained_model()
    pred = m.predict(20.0, future(5.0, 2, irr={"90_90": 300.0, "40_180": 500.0}))
    assert set(pred.contrib[0]) == {"base", "loss", "sun:90_90:window", "sun:40_180:roof", "heat"}
    assert m.group_labels() == {"sun:90_90:window": "Fenster Ost", "sun:40_180:roof": "Schräge Süd"}


def test_var0_raises_std_and_default_unchanged():
    m = trained_model()
    fut = future(5.0, 6)
    base = m.predict(20.0, fut)
    assert base.std == m.predict(20.0, fut, var0=0.0).std
    wider = m.predict(20.0, fut, var0=0.25)
    assert all(w > b for w, b in zip(wider.std, base.std))


def test_external_history_does_not_touch_model():
    m = trained_model()
    before = list(m.history)
    other = [replace(r, q=20.0) for r in before]
    a = m.predict(20.0, future(5.0, 6))
    b = m.predict(20.0, future(5.0, 6), history=other)
    assert m.history == before
    assert a.mean != b.mean  # FBH heat lags see the hot history


def test_unnamed_surface_label():
    m = OnlineZoneModel(ZoneSpec(surfaces=(SurfaceSpec(kind="wall", azimuth=270, tilt=90),)))
    assert m.group_labels() == {"sun:90_270:wall": "wall 270°/90°"}
