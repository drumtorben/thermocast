"""Burner starts per block hour (core/cycling.py) and their cost in the planner."""
from __future__ import annotations

import random

import pytest

from custom_components.thermocast.core.cycling import (
    MAX_RATE,
    CycleRate,
    closed_share,
    cycle_sample,
    prior_rates,
)
from custom_components.thermocast.core.planner import CYCLE_STARTS_PER_H, Candidate, charge_cost


def test_prior_from_the_anti_cycling_lock():
    assert prior_rates(30) == (0.0, 2.0)
    assert prior_rates(0) == prior_rates(None) == (0.0, pytest.approx(60 / 45))  # option off: 45 min
    assert CycleRate().rates(30) == (0.0, 2.0)  # nothing learned: the prior follows the option


def test_learns_the_real_rate():
    """Floor heating alone (all radiators shut) cycles 1.3×/h, open radiators take the rest (0.1/h)."""
    rng = random.Random(1)
    rate = CycleRate()
    for _ in range(300):
        closed = rng.choice([0.0, 0.25, 0.5, 0.75, 1.0])
        lam = 0.1 + 1.3 * closed
        starts = sum(1 for _ in range(20) if rng.random() < lam / 20)  # ~Poisson
        rate.update(closed, starts, 30)
    r0, r1 = rate.rates(30)
    assert r0 == pytest.approx(0.1, abs=0.15) and r1 == pytest.approx(1.3, abs=0.3)
    assert rate.n == 300


def test_rates_stay_physical():
    rate = CycleRate()
    for _ in range(50):
        rate.update(0.0, 0, 30)  # open radiators never cycle ...
        rate.update(1.0, 8, 30)  # ... a broken counter jumps
    r0, r1 = rate.rates(30)
    assert r0 >= 0.0 and r1 >= 0.0 and r0 + r1 <= MAX_RATE
    rate.update(1.5, 1, 30)  # out of range: ignored
    rate.update(0.5, -1, 30)
    assert rate.n == 100


def test_roundtrip():
    rate = CycleRate()
    rate.update(1.0, 2, 30)
    back = CycleRate.from_dict(rate.to_dict())
    assert back.n == 1 and back.rates(30) == rate.rates(30)
    assert CycleRate.from_dict(None).n == 0
    assert CycleRate.from_dict({"n": 3, "theta": "x", "p": [[1]]}).n == 0  # broken store: start over


def test_only_block_hours_are_samples():
    assert cycle_sample(1.0, 1.0, 0.0, 0.5, 2) == (0.5, 2)
    assert cycle_sample(1.0, 1.0, None, 0.5, 2) == (0.5, 2)  # no hot water entity
    assert cycle_sample(1.0, 0.2, 0.0, 0.5, 2) is None  # the block's first hour: its start is priced on its own
    assert cycle_sample(0.5, 1.0, 0.0, 0.5, 1) is None  # not a block hour
    assert cycle_sample(1.0, 1.0, 0.3, 0.5, 3) is None  # a hot water charge starts the burner too
    assert cycle_sample(1.0, 1.0, 0.0, None, 1) is None  # a valve unknown
    assert cycle_sample(1.0, 1.0, 0.0, 0.5, None) is None  # starts not followed all hour


def test_closed_share():
    assert closed_share([]) == 0.0  # no thermostat rooms: nothing to shut
    assert closed_share([1.0, 0.0, 0.5, 0.5]) == 0.5
    assert closed_share([1.0, None]) is None


def test_cost_prices_the_learned_rate():
    cand = Candidate(start=2, length=4)
    old = charge_cost(0.2)(cand, 0.0, 0.0, 6.0, 2.0)
    same = charge_cost(0.2, cycle=(0.0, CYCLE_STARTS_PER_H))(cand, 0.0, 0.0, 6.0, 2.0)
    assert old == same  # default = the former constant
    per_day = 24.0 / (2 + 4 + 6)
    start = 1.0 + 9.0 * 0.2
    parts = charge_cost(0.2, cycle=(0.25, 1.5))(cand, 0.0, 0.0, 6.0, 2.0)
    assert parts["cycling"] == pytest.approx(start * per_day * (0.25 * 4 + 1.5 * 2.0))
    assert charge_cost(0.2, cycle=(0.0, 0.0))(cand, 0.0, 0.0, 6.0, 2.0)["cycling"] == 0.0
