"""Charge and coast: upper bound, charge cap and the starts-vs-energy cost (core, no HA)."""
# ruff: noqa: I001
from __future__ import annotations

from functools import lru_cache

from .core_helpers import SPEC, future, record, trained_model
from .synthetic import simulate
from core.model import OnlineZoneModel, ZoneSpec
from core.planner import CYCLE_STARTS_PER_H, Candidate, ZonePlanInput, charge_cost, default_cost, plan
from core.rollout import ActuatorState, rollout
from core.rules import Rules

RULES = Rules(min_block_h=3, min_pause_h=2, max_switches=12)


@lru_cache
def _radiator_state() -> dict:
    """A radiator zone (heat lags 0/1 h) learned on the synthetic house – what BT-controlled rooms look like."""
    sim = simulate(days=30, seed=4)
    m = OnlineZoneModel(ZoneSpec(heat_type="radiator", surfaces=SPEC.surfaces))
    for i in range(30 * 24 - 1):
        m.update(record(sim, i), float(sim["air"][i + 1]))
    return m.to_dict()


def _radiator_model() -> OnlineZoneModel:
    m = OnlineZoneModel(ZoneSpec(heat_type="radiator", surfaces=SPEC.surfaces))
    assert m.load_dict(_radiator_state())
    return m


def _zone(t_out: float = 5.0, temp: float = 20.4, hours: int = 76, cap: float | None = None,
          high: float | None = None, floor_night: float = 18.0, radiator: bool = False) -> ZonePlanInput:
    """Hour h ends at local hour (20 + h + 1) % 24: comfort 06–22 at 20.0, otherwise the floor."""
    low = [20.0 if 6 <= (20 + h + 1) % 24 < 22 else floor_night for h in range(hours)]
    model = _radiator_model() if radiator else trained_model()
    return ZonePlanInput(
        name="z", model=model, temp_now=temp, future=future(t_out, hours), comfort_low=low,
        q_on=15.0, comfort_high=[high] * hours if high is not None else [],
        charge_cap=[cap] * hours if cap is not None else [],
    )


def test_charge_cap_stops_heating_at_the_cap():
    capped = _zone(cap=21.0, radiator=True)
    free = _zone(radiator=True)
    block = Candidate(0, 12)
    hot = free.predict(free.heating_records(block)).mean
    assert max(hot) > 21.5  # without a cap 12 h of heating overshoot clearly
    held = capped.predict(capped.heating_records(block)).mean
    assert max(held) < 21.3  # with the cap the valve closes (lagged heat may overshoot a little)
    assert all(r.q == 0.0 for r, m in zip(capped.heating_records(block)[1:], held) if m >= 21.0)


def test_no_cap_keeps_the_old_block_inputs():
    z = _zone()
    recs = z.heating_records(Candidate(2, 3))
    assert [r.q for r in recs[:6]] == [0.0, 0.0, 15.0, 15.0, 15.0, 0.0]


def test_charge_cost_parts():
    # next need 24 h from now (1 h wait + 4 h block + 19 h coast) = one start per day
    parts = charge_cost(0.8)(Candidate(1, 4), 0.5, 0.25, 19.0)
    assert parts["comfort"] == 10.0 and parts["overheat"] == 1.0
    assert abs(parts["start"] - 8.2) < 1e-9 and abs(parts["energy"] - 4 * 0.22) < 1e-9
    assert parts["delay"] == 0.0  # a late start is rewarded by the per-day scaling itself
    half = charge_cost(0.8)(Candidate(1, 4), 0.0, 0.0, 7.0)  # next need in 12 h = two starts per day
    assert abs(half["start"] - 16.4) < 1e-9
    assert charge_cost(0.0)(Candidate(None), 0.0, 0.0, 0.0) == {
        "comfort": 0.0, "overheat": 0.0, "start": 0.0, "cycling": 0.0, "energy": 0.0, "delay": 0.0,
    }
    assert default_cost(Candidate(1, 4), 0.5, 0.25, 9.0) == default_cost(Candidate(1, 4), 0.5)  # ignores both


def test_charge_cost_prices_closed_consumers_as_extra_starts():
    # one block hour with every thermostat-controlled room closed ≈ 60/45 extra burner starts (Taktsperre)
    parts = charge_cost(0.8)(Candidate(1, 4), 0.0, 0.0, 19.0, 1.5)  # per_day = 1
    assert abs(parts["cycling"] - 8.2 * CYCLE_STARTS_PER_H * 1.5) < 1e-9
    assert charge_cost(0.8)(Candidate(1, 4), 0.0, 0.0, 19.0)["cycling"] == 0.0
    assert charge_cost(0.8)(Candidate(None), 0.0, 0.0, 0.0, 0.0)["cycling"] == 0.0
    assert default_cost(Candidate(1, 4), 0.5, 0.25, 9.0, 2.0) == default_cost(Candidate(1, 4), 0.5)


def test_closed_hours_count_block_hours_with_the_valve_shut():
    z = _zone(radiator=True, cap=21.0)
    block = Candidate(0, 6)
    recs = z.heating_records(block)
    closed = sum(1 for i, r in enumerate(recs) if block.active(i) and r.q == 0.0)
    assert z.closed_hours(block, recs) == closed
    warm = _zone(radiator=True, cap=19.0, temp=20.4)  # already above its cap: closed the whole block
    assert warm.closed_hours(block, warm.heating_records(block)) == 6
    assert _zone().closed_hours(block, _zone().heating_records(block)) == 0  # no thermostat control


def test_planner_prefers_hours_when_the_consumers_are_open():
    """Two equally good blocks: the one while the radiator room may still take heat wins (fewer burner cycles)."""
    lead = _zone(t_out=3.0, temp=20.3, hours=24)
    lead = type(lead)(**{**lead.__dict__, "comfort_low": [None] * 12 + [20.0] * 12})
    room = _zone(t_out=3.0, temp=20.0, hours=24, radiator=True)
    quiet_late = [22.0] * 6 + [15.0] * 18  # quiet time from hour 6: valve held at the (low) base temperature
    room = type(room)(**{**room.__dict__, "name": "room", "leads_release": False, "comfort_low": [None] * 24,
                         "charge_cap": quiet_late})
    res = plan([lead, room], block_lengths=(3,), cost_fn=charge_cost(0.8), z=0.0)
    early = next(s for s in res.ranked if s.candidate.start == 2)
    late = next(s for s in res.ranked if s.candidate.start == 8)
    assert late.parts["cycling"] > early.parts["cycling"]


def test_closed_consumers_never_outweigh_a_real_comfort_need():
    """Cold night, every radiator room in quiet time: the screed still gets its block."""
    lead = _zone(t_out=-5.0, temp=20.1, hours=24)
    room = _zone(t_out=-5.0, temp=20.0, hours=24, radiator=True)
    room = type(room)(**{**room.__dict__, "name": "room", "leads_release": False, "comfort_low": [None] * 24,
                         "charge_cap": [15.0] * 24})
    res = plan([lead, room], cost_fn=charge_cost(0.8), z=0.0)
    assert res.best.start is not None
    assert res.ranked[0].parts["cycling"] > 0  # the closed room is priced in, but comfort wins


def test_coast_leaves_later_violations_to_the_next_block():
    from core.planner import _counted

    viol = [(2, 0.1), (10, 0.3), (11, 0.2)]
    assert _counted(viol, Candidate(0, 4), 24, coast_mode=True) == (0.1, 6.0)  # violation at 10 = next block
    assert _counted(viol, Candidate(0, 4), 24, coast_mode=False) == (0.6, 6.0)
    assert _counted(viol, Candidate(5, 4), 24, coast_mode=True) == (0.6, 1.0)  # pause < 2 h: keeps it
    assert _counted([], Candidate(0, 4), 24, coast_mode=True) == (0.0, 20.0)
    assert _counted(viol, Candidate(None), 24, coast_mode=True) == (0.6, 0.0)


def test_overheat_is_measured_for_every_zone():
    hot = _zone(t_out=-5.0, high=20.6)
    res = plan([hot], cost_fn=charge_cost(0.8), z=0.0)
    long = next(s for s in res.ranked if s.candidate.length == 12)
    assert long.parts["overheat"] > 0


def _blocks(weight: float, t_out: float = -5.0):
    zone = _zone(t_out=t_out, temp=20.3, high=22.0)
    ro = rollout([zone], 48, RULES, ActuatorState(on=False), cost_fn=charge_cost(weight), z=0.0)
    lengths = [e - s for s, e in ro.blocks]
    comfort_dip = max(
        (b - m for m, b in zip(ro.zones["z"].mean, zone.comfort_low) if b == 20.0), default=0.0
    )
    return len(lengths), (sum(lengths) / len(lengths) if lengths else 0.0), comfort_dip


def test_starts_weight_gives_fewer_longer_blocks():
    n_starts, len_starts, dip = _blocks(0.8)
    n_energy, len_energy, _ = _blocks(0.0)
    assert n_starts <= 4  # two days: at most two starts per day
    assert n_starts < n_energy and len_starts > len_energy
    assert dip < 0.15  # the comfort bound still holds (expected path, z = 0)


def test_mild_weather_needs_few_blocks_whatever_the_weight():
    """Counting the pause from now: no frequent short blocks at the upper bound, not even for 'little gas'."""
    for weight in (0.0, 0.8):
        n, _, dip = _blocks(weight, t_out=10.0)
        assert n <= 2 and dip < 0.15


def test_late_block_wins_when_the_heat_is_needed_late():
    """Nothing needed for 14 h: charging now buys no pause the house would not have had anyway – the planner
    waits and charges shortly before the need (less heat lost on the way)."""
    lead = _zone(t_out=3.0, temp=21.0, hours=24, high=22.0)
    lead = type(lead)(**{**lead.__dict__, "comfort_low": [None] * 14 + [20.8] * 10})
    res = plan([lead], cost_fn=charge_cost(0.8), z=0.0)
    assert res.best.start is not None and res.best.start >= 6  # counted from the block start it was 4
    now = next(s for s in res.ranked if s.candidate.start == 0 and s.violation == 0.0)
    assert now.cost > res.cost


def test_running_block_is_continued_without_a_new_start():
    z = _zone(t_out=3.0, temp=20.3, high=22.0, hours=24)
    res = plan([z], cost_fn=charge_cost(0.8), z=0.0, running=True)
    cont = next(s for s in res.ranked if s.candidate.start == 0)
    assert cont.candidate.continues and cont.parts["start"] == 0.0
    later = next(s for s in res.ranked if s.candidate.start == 2)
    assert not later.candidate.continues and later.parts["start"] > 0


def test_overheat_ignores_uncapped_following_zones():
    """A following radiator room without BT control is limited by its own thermostat – not the planner's business."""
    lead = _zone(t_out=-5.0, high=30.0)
    follow = _zone(t_out=-5.0, high=20.6)
    follow = type(follow)(**{**follow.__dict__, "name": "f", "leads_release": False})
    res = plan([lead, follow], cost_fn=charge_cost(0.8), z=0.0)
    assert all(s.parts["overheat"] == 0.0 for s in res.ranked)


def test_budget_realistic_four_zones_52_steps():
    import os
    import time

    zones = []
    for i in range(4):
        z = _zone(t_out=3.0, temp=20.3, high=21.5, cap=None if i == 0 else 21.5, radiator=i > 0)
        zones.append(type(z)(**{**z.__dict__, "name": f"z{i}", "leads_release": i < 2}))
    t0 = time.perf_counter()
    rollout(zones, 52, RULES, ActuatorState(on=False), cost_fn=charge_cost(0.8), z=1.0)
    elapsed = time.perf_counter() - t0
    budget = 5.0 if os.environ.get("CI") else 2.0  # dev Mac; shared CI runners are ~2× slower
    assert elapsed < budget, elapsed


def test_rollout_respects_the_charge_cap():
    ro = rollout([_zone(t_out=-5.0, cap=21.0, radiator=True)], 30, RULES, ActuatorState(on=False), cost_fn=charge_cost(0.8), z=0.0)
    assert max(ro.zones["z"].mean) < 21.3
