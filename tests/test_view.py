"""View contract: window, alignment, outlook, days, DST."""
# ruff: noqa: I001
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .core_helpers import future, trained_model
from core.planner import ZonePlanInput
from core.rollout import ActuatorState
from core.rules import Rules
from core.view import VIEW_VERSION, ZoneViewInput, align, build_view, compute_outlook, make_window

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 4, 18, 5, tzinfo=UTC)  # 20:05 local


def test_window_normal_and_dst():
    w = make_window(NOW, TZ, "Europe/Berlin")
    assert len(w.hours) == 72
    assert w.hours[0] == datetime(2026, 10, 2, 22, tzinfo=UTC)  # 03.10. 00:00 local
    assert w.hours[w.now_index] == datetime(2026, 10, 4, 18, tzinfo=UTC)
    assert len(make_window(datetime(2026, 10, 25, 12, tzinfo=UTC), TZ, "Europe/Berlin").hours) == 73
    assert len(make_window(datetime(2026, 3, 29, 12, tzinfo=UTC), TZ, "Europe/Berlin").hours) == 71


def test_align():
    w = make_window(NOW, TZ, "Europe/Berlin")
    out = align([w.hours[5], w.hours[6]], [1.0, 2.0], w.hours)
    assert out[5:7] == [1.0, 2.0] and out[4] is None and len(out) == len(w.hours)


def _comfort_at(t):
    return 20.0 if 6 <= t.astimezone(TZ).hour < 22 else None


def _view(t_out=-3.0, hours_avail=60, outlook=True, decision=None, now=NOW):
    w = make_window(now, TZ, "Europe/Berlin")
    n = len(w.hours)
    times = [w.hours[w.now_index] + timedelta(hours=h + 1) for h in range(hours_avail)]
    zpi = ZonePlanInput(
        name="eg", model=trained_model(), temp_now=20.4, future=future(t_out, hours_avail),
        comfort_low=[_comfort_at(t) for t in times], q_on=15.0,
    )
    zvi = ZoneViewInput(
        id="eg", name="EG", heat_type="fbh", leads=True,
        measured=[20.5 if i < w.now_index else None for i in range(n)],
        comfort_low=[_comfort_at(h) for h in w.hours], group_labels={"sun:90_90:window": "Fenster Ost"},
    )
    steps = n - w.now_index
    day_index = [w.hours[min(w.now_index + h, n - 1)].astimezone(TZ).toordinal() for h in range(steps)]
    out = (
        compute_outlook([zpi], steps, Rules(), ActuatorState(on=False), day_index, w.hours[w.now_index])
        if outlook else None
    )
    past = [1.0 if 3 <= i < 7 else 0.0 if i < w.now_index else None for i in range(n)]
    view = build_view(
        window=w, tz=TZ, generated_at=now,
        t_out_measured=[5.0 if i < w.now_index else None for i in range(n)],
        t_out_forecast=[t_out] * n,
        irr=[{"key": "90_90", "label": "Fenster Ost", "values": [0.0] * n}],
        heating_actual=past, release=[True if p is not None else None for p in past],
        planner=[p >= 0.5 if p is not None else None for p in past],
        zones=[zvi], outlook=out, z=1.0,
        decision=decision or {"override": "observe", "failsafe_reason": None},
        plan_change=None,
        events=[{"time": (now - timedelta(hours=3)).isoformat(), "type": "window_open", "zone": "eg"},
                {"time": (now - timedelta(days=5)).isoformat(), "type": "control_on"}],
        errors=[],
    )
    return w, view


def test_series_lengths_and_contract():
    w, v = _view()
    n = len(w.hours)
    assert v["version"] == VIEW_VERSION
    assert v["window"]["now_index"] == w.now_index and len(v["hours"]) == n
    z = v["zones"][0]
    for arr in (z["measured"], z["comfort_low"], z["plan"]["mean"], z["plan"]["std"], z["free"]["mean"], z["contrib"],
                v["weather"]["t_out"], v["heating"]["actual"]):
        assert len(arr) == n
    assert z["plan"]["mean"][w.now_index] == 20.4
    assert z["plan"]["mean"][w.now_index - 1] is None
    assert z["contrib"][w.now_index] and z["contrib"][w.now_index - 1] is None
    assert {g["kind"] for g in z["groups"]} >= {"loss", "heat"}
    assert v["weather"]["t_out_measured"][0] is True and v["weather"]["t_out_measured"][-1] is False
    json.dumps(v)  # JSON-serialisable


def test_blocks_candidates_days_events():
    w, v = _view()
    assert v["heating"]["past_blocks"] == [{"start": w.hours[3].isoformat(), "end": w.hours[7].isoformat()}]
    assert v["heating"]["planned_blocks"], "cold weather -> planned blocks"
    cands = v["candidates"]
    assert sum(c["chosen"] for c in cands) == 1
    assert any(c["start"] is None for c in cands) and len(cands) <= 6
    assert [c["cost"] for c in cands] == sorted(c["cost"] for c in cands)
    assert [d["kind"] for d in v["days"]] == ["past", "today", "future"]
    assert v["days"][0]["heat_hours"] == 4.0 and v["days"][0]["blocks"] == 1
    # release is True all past hours, the planner only during the 4 heating hours of yesterday
    assert v["days"][0]["release_followed"] == round(4 / 24, 3)
    assert v["days"][2]["heat_hours_planned"] is not None and v["days"][2]["std_end"] is not None
    assert [e["type"] for e in v["events"]] == ["window_open"]


def test_story_block_is_the_rollout_block():
    _, v = _view()
    ex = v["explanation"]
    first = v["heating"]["planned_blocks"][0]
    assert ex["next_block"] == first
    assert ex["planner_block"] is not None
    assert ex["code"] == ("heating_now" if first["start"] == v["hours"][v["window"]["now_index"]] else "block_planned")


def test_past_causes_forecast6_and_dhw():
    w = make_window(NOW, TZ, "Europe/Berlin")
    n, now = len(w.hours), w.now_index
    zvi = ZoneViewInput(
        id="eg", name="EG", heat_type="fbh", leads=True, measured=[20.0] * n, comfort_low=[None] * n,
        group_labels={}, contrib_past=[{"loss": -0.1, "heat": 0.05}] * now,
        forecast6=[20.5 if i <= now else None for i in range(n)],
    )
    v = build_view(
        window=w, tz=TZ, generated_at=NOW, t_out_measured=[None] * n, t_out_forecast=[None] * n, irr=[],
        heating_actual=[None] * n, release=[None] * n, planner=[None] * n, zones=[zvi], outlook=None, z=1.0,
        decision={"override": "observe"}, plan_change=None, events=[], errors=[],
        dhw=[0.25 if i == 3 else 0.0 for i in range(n)],
    )
    z = v["zones"][0]
    assert z["contrib"][0] == {"loss": -0.1, "heat": 0.05} and z["contrib"][now] is None
    assert {g["key"] for g in z["groups"]} == {"loss", "heat"}
    assert z["forecast6"][now] == 20.5 and z["forecast6"][now + 1] is None
    assert v["heating"]["dhw"][3] == 0.25


def test_short_forecast_and_no_outlook():
    w, v = _view(hours_avail=10)
    assert v["zones"][0]["plan"]["mean"][-1] is None
    assert v["zones"][0]["plan"]["mean"][w.now_index + 10] is not None
    _, v2 = _view(outlook=False)
    assert v2["explanation"]["code"] == "no_forecast" and v2["candidates"] == []
    assert v2["zones"][0]["plan"]["mean"][w.now_index] is None
    assert v2["days"][2]["heat_hours_planned"] is None


def test_failsafe_overrides_explanation():
    _, v = _view(decision={"override": "failsafe", "failsafe_reason": "no_temperature:EG"})
    assert v["explanation"]["code"] == "failsafe" and v["explanation"]["reason"] == "no_temperature:EG"


def test_dst_day_view():
    _, v = _view(now=datetime(2026, 10, 25, 12, tzinfo=UTC))
    assert len(v["hours"]) == 73
    assert [d["date"] for d in v["days"]] == ["2026-10-24", "2026-10-25", "2026-10-26"]
    assert len(v["zones"][0]["plan"]["mean"]) == 73
