"""Time-weighted hourly aggregation of recorder state sequences."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from . import core_helpers  # noqa: F401
from core.rules import binary_value
from core.series import hourly_fraction, hourly_mean, mask_off

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


def test_gateway_boolean_formats():
    """EMS-ESP publishes booleans as ON/OFF, true/false or (German locale) an/aus."""
    for on in ("on", "ON", "true", "an", "Ein", "1", "2.5"):
        assert binary_value(on) == 1.0, on
    for off in ("off", "OFF", "false", "aus", "0"):
        assert binary_value(off) == 0.0, off
    for unknown in ("unavailable", "unknown", "", None):
        assert binary_value(unknown) is None
