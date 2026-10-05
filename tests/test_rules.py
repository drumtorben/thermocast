"""Pure actuator rules shared by the live actuator and the rollout."""
# ruff: noqa: I001
from __future__ import annotations

import math

from . import core_helpers  # noqa: F401  (sets sys.path for `core`)
from core.rules import Rules, apply_rules, binary_value, release_state

R = Rules(min_block_h=3, min_pause_h=2, max_switches=4)


def test_turn_on_allowed_after_pause():
    assert apply_rules(True, False, math.inf, 0, R) == (True, None)
    assert apply_rules(True, False, 1.0, 0, R) == (False, "min_pause")


def test_turn_off_respects_min_block_and_budget():
    assert apply_rules(False, True, 1.0, 0, R) == (True, "min_block")
    assert apply_rules(False, True, 5.0, 4, R) == (True, "budget")
    assert apply_rules(False, True, 5.0, 3, R) == (False, None)


def test_turn_on_ignores_budget():
    assert apply_rules(True, False, 5.0, 99, R) == (True, None)


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
