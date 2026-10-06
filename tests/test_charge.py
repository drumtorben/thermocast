"""Charge and coast: upper bound, charge cap and the starts-vs-energy cost (core, no HA)."""
# ruff: noqa: I001
from __future__ import annotations

from functools import lru_cache

from .core_helpers import SPEC, future, record, trained_model
from .synthetic import simulate
from core.model import OnlineZoneModel, ZoneSpec
from core.planner import Candidate, ZonePlanInput, charge_cost, default_cost, plan
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
    parts = charge_cost(0.8)(Candidate(1, 4), 0.5, 0.25, 20.0)  # 4 h block + 20 h coast = one start per day
    assert parts["comfort"] == 10.0 and parts["overheat"] == 1.0
    assert abs(parts["start"] - 8.2) < 1e-9 and abs(parts["energy"] - 4 * 0.22) < 1e-9
    assert parts["delay"] == 0.01
    half = charge_cost(0.8)(Candidate(1, 4), 0.0, 0.0, 8.0)  # 12 h cycle = two starts per day
    assert abs(half["start"] - 16.4) < 1e-9
    assert charge_cost(0.0)(Candidate(None), 0.0, 0.0, 0.0) == {
        "comfort": 0.0, "overheat": 0.0, "start": 0.0, "energy": 0.0, "delay": 0.0,
    }
    assert default_cost(Candidate(1, 4), 0.5, 0.25, 9.0) == default_cost(Candidate(1, 4), 0.5)  # ignores both


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


def _blocks(weight: float):
    zone = _zone(t_out=3.0, temp=20.3, high=22.0)
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


def test_running_block_is_continued_without_a_new_start():
    z = _zone(t_out=3.0, temp=20.3, high=22.0, hours=24)
    res = plan([z], cost_fn=charge_cost(0.8), z=0.0, running=True)
    cont = next(s for s in res.ranked if s.candidate.start == 0)
    assert cont.candidate.continues and cont.parts["start"] == 0.0
    later = next(s for s in res.ranked if s.candidate.start == 2)
    assert not later.candidate.continues and later.parts["start"] > 0


def test_rollout_respects_the_charge_cap():
    ro = rollout([_zone(t_out=-5.0, cap=21.0, radiator=True)], 30, RULES, ActuatorState(on=False), cost_fn=charge_cost(0.8), z=0.0)
    assert max(ro.zones["z"].mean) < 21.3
