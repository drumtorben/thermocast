"""Better Thermostat target decision (core, no HA)."""
# ruff: noqa: I001
from __future__ import annotations

from dataclasses import replace

from . import core_helpers  # noqa: F401
from core.bt import MAX_WRITES_PER_DAY, BtInput, bt_decide, round_target

BASE = BtInput(
    block_on=False, failsafe=False, floor_now=20.2, high=21.5, base=17.0, quiet_now=False, quiet_soon=False,
    temp=20.6, last_written=None, writes_today=0,
)


def test_round_target_steps_and_limits():
    assert round_target(20.24) == 20.0 and round_target(20.26) == 20.5
    assert round_target(10.0) == 15.0 and round_target(30.0) == 24.0


def test_block_charges_to_the_upper_bound_otherwise_the_floor():
    assert bt_decide(replace(BASE, block_on=True)) == (21.5, "write")
    assert bt_decide(BASE) == (20.0, "write")  # floor 20.2 rounded to 0.5 K


def test_unchanged_target_is_not_written_again():
    assert bt_decide(replace(BASE, last_written=20.0)) == (None, "unchanged")


def test_open_window_holds_the_base_even_during_a_block():
    assert bt_decide(replace(BASE, block_on=True, window_open=True)) == (17.0, "write")
    assert bt_decide(replace(BASE, window_open=True)) == (17.0, "write")  # also outside a block (comfort floor)


def test_failsafe_holds_the_floor_even_during_a_block():
    assert bt_decide(replace(BASE, block_on=True, failsafe=True)) == (20.0, "write")


def test_quiet_time_off_before_it_starts_then_silence():
    """Better Thermostat keeps sending the room temperature – only an off TRV stays quiet."""
    assert bt_decide(replace(BASE, block_on=True, quiet_soon=True)) == (None, "off")  # 15 min ahead
    assert bt_decide(replace(BASE, block_on=True, quiet_now=True, last_off=True)) == (None, "quiet")


def test_missed_lead_window_still_switches_off():
    """Restart or a shifted update at 18:50: the room must not stay at its upper bound all night."""
    assert bt_decide(replace(BASE, block_on=True, quiet_now=True, last_written=21.5)) == (None, "off")


def test_quiet_time_below_the_base_holds_the_base():
    assert bt_decide(replace(BASE, quiet_soon=True, temp=16.8)) == (17.0, "write")
    assert bt_decide(replace(BASE, quiet_now=True, temp=16.8, last_written=17.0)) == (None, "unchanged")
    assert bt_decide(replace(BASE, quiet_now=True, temp=17.0, last_written=17.0)) == (None, "off")


def test_quiet_exception_for_a_cold_room_with_hysteresis():
    assert bt_decide(replace(BASE, quiet_now=True, last_off=True, temp=16.1)) == (None, "quiet")  # base 17 − 1 K
    assert bt_decide(replace(BASE, quiet_now=True, last_off=True, temp=15.9)) == (17.0, "write")
    assert bt_decide(replace(BASE, quiet_now=True, last_off=True, temp=15.9, writes_today=99)) == (17.0, "write")


def test_off_thermostat_comes_back_without_temperature_and_on_hand_back():
    assert bt_decide(replace(BASE, quiet_now=True, last_off=True, temp=None)) == (17.0, "write")
    assert bt_decide(replace(BASE, quiet_now=True, last_off=True, hand_back=True, failsafe=True)) == (17.0, "write")
    assert bt_decide(replace(BASE, last_off=True)) == (20.0, "write")  # quiet time over: on at the floor


def test_daily_budget_and_unknown_temperature():
    assert bt_decide(replace(BASE, writes_today=MAX_WRITES_PER_DAY)) == (None, "budget")
    assert bt_decide(replace(BASE, temp=None)) == (None, "no_temperature")
