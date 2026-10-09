"""Actuator rules as pure functions (shared by the live actuator and the rollout)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Rules:
    min_block_h: float = 3.0
    min_pause_h: float = 2.0
    max_switches: int = 12


def apply_rules(
    want: bool, current: bool, elapsed_h: float, switches_today: int, rules: Rules, next_on_h: float | None = None
) -> tuple[bool, str | None]:
    """Turning ON is always allowed except during the minimum pause (anti short-cycling);
    turning OFF needs the minimum block and budget left (fail-safe direction = heating allowed).

    ``next_on_h``: hours until the planner's next block. If it starts before the minimum pause would be over,
    the block is bridged instead of ended – the pause would only delay that block and cost a start."""
    if current and not want:
        if elapsed_h < rules.min_block_h:
            return True, "min_block"
        if switches_today >= rules.max_switches:
            return True, "budget"
        if next_on_h is not None and next_on_h < rules.min_pause_h:
            return True, "bridge"
    elif not current and want and elapsed_h < rules.min_pause_h:
        return False, "min_pause"
    return want, None


def hold_until(override: str | None, last_change: datetime | None, rules: Rules) -> datetime | None:
    """When the minimum block or pause that holds back the planner's wish is over."""
    if last_change is None:
        return None
    hours = {"min_block": rules.min_block_h, "min_pause": rules.min_pause_h}.get(override or "")
    return None if hours is None else last_change + timedelta(hours=hours)


def next_on_hours(block_start: datetime | None, now: datetime) -> float | None:
    """Hours until the planned block starts; None if none is planned or it is already running."""
    if block_start is None or block_start <= now:
        return None
    return (block_start - now).total_seconds() / 3600


TAIL_MIN = timedelta(minutes=15)  # a burner run shorter than this at the very end of a block is not worth a start


def trim_tail_restart(
    now: datetime, block_end: datetime | None, last_start: datetime | None, lock: timedelta, tick: timedelta
) -> bool:
    """End the block now: the boiler's anti-cycling lock ends before the next update and the restart that
    follows would only run a few minutes before the block ends anyway (one burner start saved)."""
    if block_end is None or last_start is None:
        return False
    restart = last_start + lock
    return now < restart <= now + tick and block_end - restart < TAIL_MIN


WINDOW_RECOVERY = timedelta(hours=1)  # airing cools the air, not the walls: the air is back within ~45 min


def window_recovering(closed_at: datetime | None, now: datetime, recovery: timedelta = WINDOW_RECOVERY) -> bool:
    """A window of the zone closed less than ``recovery`` ago."""
    return closed_at is not None and timedelta(0) <= now - closed_at < recovery


def window_recovery_temp(
    temp: float, before: float | None, closed_at: datetime | None, now: datetime, recovery: timedelta = WINDOW_RECOVERY
) -> float:
    """Temperature to plan from: after airing, the walls and furniture warm the air back up within the hour –
    until then the temperature from before the window opened counts (never less than the measurement)."""
    if before is None or not window_recovering(closed_at, now, recovery):
        return temp
    return max(temp, before)


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
