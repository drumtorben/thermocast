"""Controller rollout until the end of tomorrow."""
# ruff: noqa: I001
from __future__ import annotations

import time
from dataclasses import replace

from .core_helpers import future, trained_model
from core.planner import ZonePlanInput, plan
from core.rollout import ActuatorState, block_lengths_for, rollout
from core.rules import Rules

RULES = Rules(min_block_h=3, min_pause_h=2, max_switches=12)


def _zone(name: str, t_out: float, temp_now: float, hours: int = 76, leads: bool = True) -> ZonePlanInput:
    comfort = [20.0 if 6 <= (20 + h + 1) % 24 < 22 else None for h in range(hours)]
    return ZonePlanInput(
        name=name, model=trained_model(), temp_now=temp_now, future=future(t_out, hours),
        comfort_low=comfort, q_on=15.0, leads_release=leads,
    )


def test_first_step_equals_live_plan():
    zones = [_zone("eg", -3.0, 20.4)]
    ro = rollout(zones, 52, RULES, ActuatorState(on=False))
    live = plan([replace(z, future=z.future[:24], comfort_low=z.comfort_low[:24]) for z in zones])
    assert ro.first.best == live.best
    assert ro.first.cost == live.cost


def test_cold_produces_blocks_and_warm_none():
    cold = rollout([_zone("eg", -5.0, 20.4)], 52, RULES, ActuatorState(on=False))
    assert cold.blocks, "expected heating blocks in cold weather"
    warm = rollout([_zone("eg", 24.0, 21.5)], 52, RULES, ActuatorState(on=False))
    assert warm.blocks == []
    assert len(warm.on) == 52


def test_rules_respected():
    ro = rollout([_zone("eg", -5.0, 20.4)], 52, RULES, ActuatorState(on=False))
    for start, end in ro.blocks:
        if end < len(ro.on):  # block finished inside the rollout
            assert end - start >= RULES.min_block_h
    for (_, e1), (s2, _) in zip(ro.blocks, ro.blocks[1:]):
        assert s2 - e1 >= RULES.min_pause_h


def test_running_block_is_continued_until_min_block():
    ro = rollout([_zone("eg", 18.0, 21.5)], 10, RULES, ActuatorState(on=True, since_h=1.0))
    assert ro.on[:2] == [True, True]
    assert ro.on[2] is False


def test_trajectories_and_contributions():
    ro = rollout([_zone("eg", -5.0, 20.4)], 30, RULES, ActuatorState(on=False))
    tr = ro.zones["eg"]
    assert len(tr.mean) == len(tr.std) == len(tr.contrib) == len(tr.free_mean) == 30
    prev = tr.temp0
    for m, c in zip(tr.mean, tr.contrib):
        assert abs(sum(c.values()) - (m - prev)) < 1e-9
        prev = m
    assert all(b >= a for a, b in zip(tr.std, tr.std[1:]))  # uncertainty carried across re-plans


def test_short_forecast_truncates_steps():
    ro = rollout([_zone("eg", -5.0, 20.4, hours=20)], 52, RULES, ActuatorState(on=False))
    assert len(ro.on) == 20


def test_block_lengths_for():
    assert block_lengths_for(3) == (3, 4, 6, 8)
    assert block_lengths_for(9) == (9,)


def test_budget_four_zones_52_steps():
    zones = [_zone(f"z{i}", -5.0, 20.3, leads=i < 2) for i in range(4)]
    t0 = time.perf_counter()
    rollout(zones, 52, RULES, ActuatorState(on=False))
    elapsed = time.perf_counter() - t0
    assert elapsed < 2.0, elapsed


def test_first_step_equals_live_plan_with_calibration():
    scale = tuple(1.0 + 0.1 * i for i in range(48))
    zones = [replace(_zone("eg", -3.0, 20.4), std_scale=scale)]
    ro = rollout(zones, 52, RULES, ActuatorState(on=False))
    live = plan([replace(z, future=z.future[:24], comfort_low=z.comfort_low[:24]) for z in zones])
    assert ro.first.best == live.best and ro.first.cost == live.cost
    assert ro.zones["eg"].std[0] == zones[0].model.predict(20.4, zones[0].future[:1]).std[0]  # raw σ in the log
