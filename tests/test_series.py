"""Time-weighted hourly aggregation of recorder state sequences."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from . import core_helpers  # noqa: F401
from core.rules import binary_value
from core.series import HourlyIntegral, hourly_fraction, hourly_mean, mask_off

T0 = datetime(2026, 10, 3, 22, tzinfo=UTC)
HOURS = [T0 + timedelta(hours=i) for i in range(4)]


def test_time_weighted_mean_and_future_none():
    series = [(T0 - timedelta(minutes=5), "20.0"), (T0 + timedelta(minutes=30), "21.0")]
    end = T0 + timedelta(hours=1, minutes=30)
    assert hourly_mean(series, HOURS, end) == [20.5, 21.0, None, None]


def test_unavailable_segments_are_skipped():
    series = [(T0, "20.0"), (T0 + timedelta(minutes=30), "unavailable")]
    assert hourly_mean(series, HOURS[:1], T0 + timedelta(hours=1)) == [20.0]
    assert hourly_mean([(T0, "unavailable")], HOURS[:1], T0 + timedelta(hours=1)) == [None]


def test_empty_series():
    assert hourly_mean([], HOURS, T0 + timedelta(hours=4)) == [None] * 4


def test_on_fraction():
    series = [(T0, "off"), (T0 + timedelta(minutes=15), "on"), (T0 + timedelta(minutes=45), "off")]
    assert hourly_fraction(series, HOURS[:2], T0 + timedelta(hours=2), binary_value) == [0.5, 0.0]


def test_pump_minus_dhw_charging():
    """The combi boiler's pump also runs for hot water – that time is not space heating."""
    m = lambda k: T0 + timedelta(minutes=k)
    pump = [(m(-10), "on"), (m(70), "off"), (m(100), "unavailable")]
    dhw = [(m(-30), "off"), (m(20), "on"), (m(35), "off"), (m(80), "on"), (m(90), "off")]
    heating = mask_off(pump, dhw, binary_value)
    assert heating == [(m(-10), "on"), (m(20), "off"), (m(35), "on"), (m(70), "off"), (m(100), "unavailable")]
    end = T0 + timedelta(hours=2)
    assert hourly_fraction(heating, HOURS[:2], end, binary_value) == [0.75, round(10 / 40, 3)]
    assert mask_off(pump, [], binary_value) == pump
    assert mask_off([], dhw, binary_value) == []


def test_thermostat_heating_share():
    from core.rules import hvac_heating

    m = lambda k: T0 + timedelta(minutes=k)
    actions = [(m(0), "idle"), (m(15), "heating"), (m(30), "idle"), (m(70), "")]
    assert hourly_fraction(actions, HOURS[:2], T0 + timedelta(hours=2), hvac_heating) == [0.25, 0.0]
    assert hvac_heating("preheating") == 1.0 and hvac_heating(None) is None


def test_counter_increase_per_hour():
    from core.series import hourly_increase

    m = lambda k: T0 + timedelta(minutes=k)
    starts = [(m(-30), "100"), (m(20), "101"), (m(50), "103"), (m(70), "unavailable"), (m(80), "104"), (m(130), "0")]
    # hour 0: 100 -> 103; hour 1: 103 -> 104 (unavailable skipped); hour 2: counter reset -> no negative count
    assert hourly_increase(starts, HOURS[:3], T0 + timedelta(hours=2, minutes=30)) == [3.0, 1.0, 0.0]
    assert hourly_increase([], HOURS[:2], T0 + timedelta(hours=2)) == [None, None]


def test_gateway_boolean_formats():
    """EMS-ESP publishes booleans as ON/OFF, true/false or (German locale) an/aus."""
    for on in ("on", "ON", "true", "an", "Ein", "1", "2.5"):
        assert binary_value(on) == 1.0, on
    for off in ("off", "OFF", "false", "aus", "0"):
        assert binary_value(off) == 0.0, off
    for unknown in ("unavailable", "unknown", "", None):
        assert binary_value(unknown) is None


def _m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_hourly_integral_weights_a_cycling_burner_by_time():
    """A 6-min burner run (flow 27 K above the room) in an hour of 4 K: four samples could give 4 or 10.6."""
    q = HourlyIntegral()
    q.set(_m(0), 4.0)
    q.set(_m(20), 27.0)
    q.set(_m(26), 4.0)
    q.set(_m(45), 4.0)  # the 15-min update
    q.set(_m(60), 4.0)
    assert q.mean(T0) == pytest.approx((54 * 4.0 + 6 * 27.0) / 60)


def test_hourly_integral_splits_at_the_hour_and_skips_gaps():
    q = HourlyIntegral()
    q.set(_m(50), 10.0)
    q.set(_m(70), None)  # unavailable: not counted
    q.set(_m(80), 2.0)
    q.set(_m(90), 2.0)
    assert q.mean(T0) == 10.0  # only 10 min covered, all of them at 10
    assert q.mean(T0 + timedelta(hours=1)) == pytest.approx((10 * 10.0 + 10 * 2.0) / 20)
    assert q.mean(T0 + timedelta(hours=2)) is None


def test_hourly_integral_holds_a_value_only_so_long():
    q = HourlyIntegral(max_hold=timedelta(minutes=30))
    q.set(_m(0), 5.0)
    q.set(_m(60), 0.0)  # the feed was silent: only 30 min count
    assert q.mean(T0) == 5.0
    q.set(_m(30), 99.0)  # back in time: ignored
    assert q.mean(T0) == 5.0


def test_hourly_integral_survives_a_restart_without_holding_across_it():
    q = HourlyIntegral()
    q.set(_m(0), 6.0)
    q.set(_m(15), 6.0)
    restored = HourlyIntegral()
    restored.load(q.to_dict())
    restored.set(_m(45), 0.0)  # first feed after the restart: 15–45 is a gap
    restored.set(_m(60), 0.0)
    assert restored.mean(T0) == pytest.approx((15 * 6.0) / 30)


def test_hourly_integral_forgets_old_hours():
    q = HourlyIntegral(keep_hours=2)
    q.set(_m(0), 1.0)
    q.set(_m(10), 1.0)
    for h in range(1, 5):
        q.set(_m(60 * h), 1.0)
    assert q.mean(T0) is None and q.mean(T0 + timedelta(hours=3)) == 1.0


def test_hourly_integral_needs_some_coverage_when_asked():
    q = HourlyIntegral()
    q.set(_m(59), 1.0)
    q.set(_m(60), 1.0)
    assert q.mean(T0) == 1.0
    assert q.mean(T0, min_covered=timedelta(minutes=15)) is None
