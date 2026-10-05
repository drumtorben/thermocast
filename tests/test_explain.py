"""Explanation codes, decision robustness and plan-change attribution."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from .core_helpers import future, trained_model
from core.explain import PlanSnapshot, explain, plan_change, robustness, snapshot_from
from core.planner import Candidate, ZonePlanInput, plan

H0 = datetime(2026, 10, 4, 18, tzinfo=UTC)


def _zone(t_out: float, temp_now: float, irr: dict | None = None, hours: int = 24) -> ZonePlanInput:
    return ZonePlanInput(
        name="eg", model=trained_model(), temp_now=temp_now, future=future(t_out, hours, irr),
        comfort_low=[20.0] * hours, q_on=15.0,
    )


def test_block_planned_or_heating_now_when_cold():
    zones = [_zone(-5.0, 20.4)]
    res = plan(zones)
    ex = explain(res, zones, H0, 1.0)
    assert ex["code"] in ("block_planned", "heating_now")
    assert ex["driver"] == "eg"
    assert ex["next_block"]["start"] == (H0 + timedelta(hours=res.best.start)).isoformat()
    assert ex["first_violation"] is not None and ex["deficit_k"] > 0


def test_no_need_when_warm():
    zones = [_zone(24.0, 21.5)]
    ex = explain(plan(zones), zones, H0, 1.0)
    assert ex == {"code": "no_need", "driver": None, "first_violation": None, "deficit_k": None,
                  "next_block": None, "lead_h": None}


def test_no_need_sun_when_only_sun_keeps_it_warm():
    # find an outdoor temperature where the dark day violates comfort but the sunny one does not
    for t_out in (16.0, 14.0, 12.0, 10.0):
        dark = _zone(t_out, 20.6, hours=8)
        sunny = _zone(t_out, 20.6, {"90_90": 700.0, "40_180": 900.0}, hours=8)
        if plan([dark]).violation_free["eg"] > 0 and plan([sunny]).violation_free["eg"] == 0:
            assert explain(plan([sunny]), [sunny], H0, 1.0)["code"] == "no_need_sun"
            return
    raise AssertionError("no outdoor temperature separated sunny from dark")


def test_robustness_close_and_sigma_driven():
    zones = [_zone(-5.0, 20.4)]
    res, res0 = plan(zones), plan(zones, z=0.0)
    rb = robustness(res, res0)
    assert rb["level"] in ("clear", "close") and rb["margin"] >= 0
    no_block = replace(res0, best=Candidate(None))
    assert robustness(res, no_block)["sigma_driven"] is (res.best.start is not None)


def _snap(block_start: int | None, t_out: float, hour0: datetime = H0, expected: float = 20.0) -> PlanSnapshot:
    blk = None
    if block_start is not None:
        blk = (hour0 + timedelta(hours=block_start), hour0 + timedelta(hours=block_start + 4))
    hours = [hour0 + timedelta(hours=i) for i in range(24)]
    return PlanSnapshot(hour0, blk, {h: t_out for h in hours}, {h: 0.0 for h in hours}, {"eg": expected})


def test_plan_change_none_without_previous_or_same_block():
    assert plan_change(None, _snap(5, 3.0), {}) is None
    assert plan_change(_snap(5, 3.0), _snap(5, 3.0), {}) is None


def test_plan_change_attributes_outdoor_temperature():
    prev = _snap(5, 3.0)
    cur = _snap(3, 1.5, hour0=H0 + timedelta(hours=1))  # absolute start moves 23:00 -> 22:00
    ch = plan_change(prev, cur, {"eg": 20.0})
    assert ch["previous_block"]["start"] == (H0 + timedelta(hours=5)).isoformat()
    assert ch["cause"]["kind"] == "t_out" and ch["cause"]["delta"] == -1.5


def test_plan_change_attributes_room_temperature():
    prev = _snap(5, 3.0, expected=20.5)
    cur = _snap(None, 3.0, hour0=H0 + timedelta(hours=1))
    ch = plan_change(prev, cur, {"eg": 21.3})
    assert ch["current_block"] is None
    assert ch["cause"] == {"kind": "room", "delta": 0.8, "at": None, "zone": "eg"}


def test_snapshot_from_plan():
    zones = [_zone(-5.0, 20.4)]
    res = plan(zones)
    snap = snapshot_from(res, zones, H0)
    assert snap.t_out[H0] == -5.0
    assert set(snap.expected_next) == {"eg"}
