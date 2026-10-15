"""Vacation on the synthetic house: the rollout holds the lowered floor and heats up for the return."""
# ruff: noqa: I001
from __future__ import annotations

from .core_helpers import future, trained_model
from core.planner import ZonePlanInput, charge_cost
from core.rollout import ActuatorState, RolloutResult, rollout
from core.rules import Rules

RULES = Rules(min_block_h=3, min_pause_h=2, max_switches=12)
AWAY_H, LEAD_H = 24, 12  # away until hour 24, preheat lead 12 h, return at hour 36
RETURN_H = AWAY_H + LEAD_H
STEPS, HOURS = 48, 72  # rollout steps; inputs cover steps + the planner's 24-h lookahead
FLOOR, BASE, COMFORT = 15.0, 18.0, 20.0


def _rollout(t_out: float) -> RolloutResult:
    """Bounds per hour as the coordinator builds them (comfort all day from the return on)."""
    low = [FLOOR if h < AWAY_H else BASE if h < RETURN_H else COMFORT for h in range(HOURS)]
    high = [FLOOR + 1 if h < AWAY_H else COMFORT + 1 for h in range(HOURS)]
    zone = ZonePlanInput(
        name="eg", model=trained_model(), temp_now=16.0, future=future(t_out, HOURS),
        comfort_low=low, comfort_high=high, q_on=15.0,
    )
    return rollout([zone], STEPS, RULES, ActuatorState(on=False), cost_fn=charge_cost(0.8))


def test_mild_weather_holds_the_floor_and_is_warm_for_the_return():
    ro = _rollout(5.0)
    tr = ro.zones["eg"]
    assert not any(ro.on[:6])  # away: cools towards the floor first
    assert min(tr.mean[:AWAY_H]) >= FLOOR - 0.3, tr.mean[:AWAY_H]
    assert max(tr.mean[:AWAY_H]) <= FLOOR + 1.5, tr.mean[:AWAY_H]  # no charging while away
    assert min(tr.mean[RETURN_H:STEPS]) >= COMFORT - 0.3, tr.mean[RETURN_H:STEPS]


def test_cold_weather_heats_through_the_whole_lead():
    """At −5 °C the synthetic floor heating gains ~0.25 K/h: 12 h are too short for 15 → 20 °C, so the lead
    heats without a pause (and starts before it) – a longer lead or a higher floor is the user's lever."""
    ro = _rollout(-5.0)
    tr = ro.zones["eg"]
    assert min(tr.mean[:AWAY_H]) >= FLOOR - 0.3, tr.mean[:AWAY_H]
    assert all(ro.on[AWAY_H:RETURN_H])
    assert tr.mean[RETURN_H] >= COMFORT - 1.0
