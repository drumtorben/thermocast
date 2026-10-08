"""Warm start from hourly history: record building and training."""
# ruff: noqa: I001
from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .core_helpers import SPEC
from .synthetic import simulate
from core.model import OnlineZoneModel
from core.quality import hindcast, hindcast_metrics
from core.warmstart import WarmstartInputs, build_records, warm_train

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 9, 4, 22, tzinfo=UTC)


def _inputs(sim, hours: int, heating_from_pump: bool = True) -> WarmstartInputs:
    q = sim["q"][:hours]
    return WarmstartInputs(
        hours=[T0 + timedelta(hours=i) for i in range(hours)],
        temp=[float(x) for x in sim["air"][:hours]],
        t_out=[float(x) for x in sim["t_out"][:hours]],
        irr={k: [float(x) for x in v[:hours]] for k, v in sim["irr"].items()},
        # the synthetic house heats with q = 35 - air: a flow of 35 °C while the pump runs
        flow=[35.0] * hours,
        heating=[1.0 if x > 0 else 0.0 for x in q] if heating_from_pump else None,
    )


def test_records_mirror_live_heating_proxy():
    sim = simulate(days=3, seed=1)
    recs = build_records(_inputs(sim, 48), ["90_90", "40_180"])
    assert len(recs) == 47
    for i, (t, rec, nxt) in enumerate(recs):
        assert t == T0 + timedelta(hours=i)
        assert rec.q == (35.0 - float(sim["air"][i]) if sim["q"][i] > 0 else 0.0)
        assert nxt == float(sim["air"][i + 1]) and rec.valid


def test_missing_data_and_open_windows_are_not_learned():
    sim = simulate(days=2, seed=1)
    inp = _inputs(sim, 10)
    inp.temp[3] = None
    inp.t_out[6] = None
    inp.window = [[0.0] * 8 + [0.5, 0.0]]
    recs = build_records(inp, ["90_90", "40_180"])
    invalid = [i for i, (_, rec, _) in enumerate(recs) if not rec.valid]
    # temp_next missing, temp missing, t_out missing, aired in the next hour (end temperature), window open
    assert invalid == [2, 3, 6, 7, 8]
    inp.window = [[0.0, 1.0] + [0.0] * 8]
    after = [i for i, (_, rec, _) in enumerate(build_records(inp, ["90_90", "40_180"])) if not rec.valid]
    assert after[:3] == [0, 1, 2]  # before, during and after the airing (the air still recovers)
    recs_no_irr = build_records(_inputs(sim, 5), ["90_90", "90_270"])  # unknown orientation
    assert not any(rec.valid for _, rec, _ in recs_no_irr)


def test_warm_train_learns_a_useful_model_and_rebuilds_logs():
    sim = simulate(days=31, seed=2)
    recs = build_records(_inputs(sim, 30 * 24), ["90_90", "40_180"])
    res = warm_train(OnlineZoneModel(SPEC), recs, TZ)
    assert res.learned == len(recs)
    assert res.model.mae < 0.08
    assert len(res.log) == 14 * 24 and len(res.params) == 30
    assert res.q_on is not None and 5 < res.q_on < 20
    hc = hindcast(res.model, res.log, TZ)
    assert hindcast_metrics(res.log, hc, since=None)["mae"] < 0.6


def test_without_pump_entity_flow_means_heating():
    sim = simulate(days=1, seed=1)
    recs = build_records(_inputs(sim, 5, heating_from_pump=False), ["90_90", "40_180"])
    assert all(math.isclose(rec.q, 35.0 - float(sim["air"][i])) for i, (_, rec, _) in enumerate(recs))
