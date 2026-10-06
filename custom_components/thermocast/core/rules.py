"""Actuator rules as pure functions (shared by the live actuator and the rollout)."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Rules:
    min_block_h: float = 3.0
    min_pause_h: float = 2.0
    max_switches: int = 12


def apply_rules(
    want: bool, current: bool, elapsed_h: float, switches_today: int, rules: Rules
) -> tuple[bool, str | None]:
    """Turning ON is always allowed except during the minimum pause (anti short-cycling);
    turning OFF needs the minimum block and budget left (fail-safe direction = heating allowed)."""
    if current and not want:
        if elapsed_h < rules.min_block_h:
            return True, "min_block"
        if switches_today >= rules.max_switches:
            return True, "budget"
    elif not current and want and elapsed_h < rules.min_pause_h:
        return False, "min_pause"
    return want, None


def release_state(value: str | None, domain: str, on_value: str | None, off_value: str | None) -> bool | None:
    """Interpret a state of the release entity: True = heating allowed, None = unknown."""
    if value is None or value in ("unknown", "unavailable"):
        return None
    if domain in ("select", "input_select"):
        if value == on_value:
            return True
        if value == off_value:
            return False
        return None
    if domain in ("number", "input_number"):
        try:
            v, on_v, off_v = float(value), float(on_value), float(off_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return None
        if abs(v - on_v) < 1e-6:
            return True
        if abs(v - off_v) < 1e-6:
            return False
        return None
    return value == "on"


def hvac_heating(action: str | None) -> float | None:
    """``hvac_action`` of a thermostat -> 1.0 while its valve heats, 0.0 otherwise, None if unknown.

    Used as the radiator valve signal: TRVs like the Sonoff TRVZB report no real valve position
    (``valve_opening_degree`` is only a configured limit), but they do report whether they are heating.
    """
    if action in ("heating", "preheating"):
        return 1.0
    if action in ("idle", "off", "cooling", "drying", "fan", "defrosting"):
        return 0.0
    return None


_ON = frozenset({"on", "true", "an", "ein", "yes", "ja"})
_OFF = frozenset({"off", "false", "aus", "no", "nein"})


def binary_value(value: str | None) -> float | None:
    """on/off or numeric (> 0 = on) state -> 1.0 / 0.0, None if unknown.

    Also accepts the boolean formats of gateways that publish plain sensors (EMS-ESP: ON/OFF, true/false, an/aus).
    """
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _ON:
            return 1.0
        if word in _OFF:
            return 0.0
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return (1.0 if v > 0 else 0.0) if math.isfinite(v) else None
