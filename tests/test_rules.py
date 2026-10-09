"""Pure actuator rules shared by the live actuator and the rollout."""
# ruff: noqa: I001
from __future__ import annotations

import math

from . import core_helpers  # noqa: F401  (sets sys.path for `core`)
from datetime import UTC, datetime, timedelta

from core.rules import (
    Rules,
    apply_rules,
    binary_value,
    hold_until,
    next_on_hours,
    release_state,
    trim_tail_restart,
    window_recovering,
    window_recovery_temp,
)

R = Rules(min_block_h=3, min_pause_h=2, max_switches=4)


def test_turn_on_allowed_after_pause():
    assert apply_rules(True, False, math.inf, 0, R) == (True, None)
    assert apply_rules(True, False, 1.0, 0, R) == (False, "min_pause")


def test_turn_off_respects_min_block_and_budget():
    assert apply_rules(False, True, 1.0, 0, R) == (True, "min_block")
    assert apply_rules(False, True, 5.0, 4, R) == (True, "budget")
    assert apply_rules(False, True, 5.0, 3, R) == (False, None)


def test_short_pause_is_bridged():
    # the next block would start before the 2 h pause is over: ending the block only delays it
    assert apply_rules(False, True, 5.0, 0, R, next_on_h=1.8) == (True, "bridge")
    assert apply_rules(False, True, 5.0, 0, R, next_on_h=2.5) == (False, None)
    assert apply_rules(False, True, 5.0, 0, R, next_on_h=None) == (False, None)
    assert apply_rules(False, True, 1.0, 0, R, next_on_h=1.0) == (True, "min_block")  # owed block first
    assert apply_rules(False, False, 5.0, 0, R, next_on_h=1.0) == (False, None)  # already off: nothing to bridge


def test_next_on_hours():
    now = datetime(2026, 10, 9, 7, 12, 40, tzinfo=UTC)
    assert next_on_hours(datetime(2026, 10, 9, 9, tzinfo=UTC), now) == (timedelta(hours=1, minutes=47, seconds=20)
                                                                        .total_seconds() / 3600)
    assert next_on_hours(datetime(2026, 10, 9, 7, tzinfo=UTC), now) is None  # running block (e.g. trimmed)
    assert next_on_hours(None, now) is None


def test_hold_until():
    t = datetime(2026, 10, 9, 7, 12, 40, tzinfo=UTC)
    assert hold_until("min_pause", t, R) == t + timedelta(hours=2)
    assert hold_until("min_block", t, R) == t + timedelta(hours=3)
    assert hold_until("budget", t, R) is None
    assert hold_until("min_pause", None, R) is None


def test_turn_on_ignores_budget():
    assert apply_rules(True, False, 5.0, 99, R) == (True, None)


def test_trim_tail_restart_ends_the_block_before_a_short_last_burner_run():
    t0 = datetime(2026, 10, 7, 0, 42, tzinfo=UTC)  # last burner start 02:42 in local terms -> here: t0
    lock = timedelta(minutes=45)
    end = t0 + timedelta(minutes=46)  # restart would come 1 min before the block ends
    tick = timedelta(minutes=15)
    assert trim_tail_restart(t0 + timedelta(minutes=31), end, t0, lock, tick)  # restart within the next tick
    assert not trim_tail_restart(t0 + timedelta(minutes=20), end, t0, lock, tick)  # next tick decides
    assert not trim_tail_restart(t0 + timedelta(minutes=31), t0 + timedelta(minutes=75), t0, lock, tick)  # long run
    assert not trim_tail_restart(t0 + timedelta(minutes=50), end, t0, lock, tick)  # lock over: burner may be running
    assert not trim_tail_restart(t0 + timedelta(minutes=31), None, t0, lock, tick)  # no block end known
    assert not trim_tail_restart(t0 + timedelta(minutes=31), end, None, lock, tick)  # no burner start known


def test_release_state():
    assert release_state("16.0", "number", "16", "10") is True
    assert release_state("10", "input_number", "16", "10") is False
    assert release_state("12", "number", "16", "10") is None
    assert release_state("winter", "select", "winter", "summer") is True
    assert release_state("auto", "select", "winter", "summer") is None
    assert release_state("on", "switch", None, None) is True
    assert release_state("unavailable", "switch", None, None) is None
    assert release_state(None, "switch", None, None) is None


def test_binary_value():
    assert binary_value("on") == 1.0
    assert binary_value("off") == 0.0
    assert binary_value("42") == 1.0
    assert binary_value("0") == 0.0
    assert binary_value("unknown") is None


def test_window_recovery_plans_from_the_temperature_before_airing():
    closed = datetime(2026, 10, 8, 5, 50, tzinfo=UTC)
    # real case: 21.9 °C before airing, 20.9 °C after, back to 21.4 °C after 40 min
    assert window_recovery_temp(20.9, 21.9, closed, closed + timedelta(minutes=7)) == 21.9
    assert window_recovery_temp(22.3, 21.9, closed, closed + timedelta(minutes=30)) == 22.3  # never below the room
    assert window_recovery_temp(20.9, 21.9, closed, closed + timedelta(minutes=60)) == 20.9  # recovery over
    assert window_recovery_temp(20.9, None, closed, closed + timedelta(minutes=7)) == 20.9  # nothing known before
    assert window_recovery_temp(20.9, 21.9, None, closed) == 20.9  # never aired
    assert window_recovering(closed, closed) and not window_recovering(closed, closed - timedelta(minutes=1))
