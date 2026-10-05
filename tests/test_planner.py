"""Planner: cost components, ranking, all-zone planned predictions, shortcut."""
# ruff: noqa: I001
from __future__ import annotations

from .core_helpers import future, trained_model
from core.planner import Candidate, ZonePlanInput, default_cost, plan, violations


def _zone(name: str, t_out: float, temp_now: float, leads: bool = True) -> ZonePlanInput:
    return ZonePlanInput(
        name=name, model=trained_model(), temp_now=temp_now, future=future(t_out, 24),
        comfort_low=[20.0] * 24, q_on=15.0, leads_release=leads,
    )


def test_default_cost_components_match_old_scalar():
    parts = default_cost(Candidate(3, 4), 0.5)
    assert parts == {"comfort": 10.0, "start": 1.0, "energy": 0.15 * 4, "delay": 0.01 * 3}
    assert default_cost(Candidate(None), 0.0) == {"comfort": 0.0, "start": 0.0, "energy": 0.0, "delay": 0.0}


def test_ranked_is_sorted_and_best_first():
    res = plan([_zone("eg", -5.0, 20.3)])
    costs = [s.cost for s in res.ranked]
    assert costs == sorted(costs)
    assert res.ranked[0].candidate == res.best
    assert res.cost == res.ranked[0].cost
    assert sum(res.ranked[0].parts.values()) == res.cost
    assert any(s.candidate.start is None for s in res.ranked)


def test_planned_covers_following_zones():
    res = plan([_zone("eg", -5.0, 20.3), _zone("eltern", -5.0, 19.0, leads=False)])
    assert res.best.start is not None
    assert set(res.planned) == {"eg", "eltern"}
    assert res.planned["eltern"].mean != res.free_run["eltern"].mean


def test_shortcut_identical_when_no_violation():
    zones = [_zone("eg", 18.0, 21.5)]
    full, short = plan(zones), plan(zones, shortcut=True)
    assert full.best == short.best == Candidate(None)
    assert full.cost == short.cost
    assert len(short.ranked) == 1


def test_shortcut_ignored_when_violation():
    zones = [_zone("eg", -5.0, 20.3)]
    assert plan(zones, shortcut=True).best == plan(zones).best


def test_violations_lists_index_and_deficit():
    res = plan([_zone("eg", -5.0, 20.3)])
    v = violations(res.free_run["eg"], [20.0] * 24, 1.0)
    assert v and v[0][0] >= 0 and all(d > 0 for _, d in v)


def test_std_scale_makes_the_planner_more_cautious():
    from dataclasses import replace

    zone = _zone("eg", 2.0, 20.5)
    wide = replace(zone, std_scale=(3.0,) * 24)
    assert plan([wide]).violation_free["eg"] > plan([zone]).violation_free["eg"]
    shifted = replace(zone, std_scale=(1.0,) * 5 + (3.0,) * 19, scale_offset=5)
    assert shifted.predict(zone.future).std[0] == 3 * zone.predict(zone.future).std[0]
