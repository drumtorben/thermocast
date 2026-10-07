"""Which target to give a zone's Better Thermostat – a pure decision (no HA imports).

During a heating block the room may charge up to its upper bound; otherwise it is held at its lower
bound (comfort − band in comfort time, the base temperature outside). Quiet time: the base temperature
is set once shortly before, then nothing is written (valve noise) – unless a leading zone gets cold.
"""
from __future__ import annotations

from dataclasses import dataclass

STEP = 0.5  # K, the usual thermostat resolution
MIN_TARGET, MAX_TARGET = 15.0, 24.0  # hard limits, whatever the configuration says
MAX_WRITES_PER_DAY = 24
COLD_MARGIN = 1.0  # K below the base temperature: quiet time is broken for a leading zone


def round_target(value: float) -> float:
    return min(MAX_TARGET, max(MIN_TARGET, round(value / STEP) * STEP))


@dataclass(frozen=True)
class BtInput:
    block_on: bool  # the heat source is released (a block runs)
    failsafe: bool  # fail-safe: hold the lower bound, never charge
    floor_now: float  # current lower bound of the zone
    high: float  # upper bound (charge target)
    base: float  # base temperature (quiet time)
    quiet_now: bool
    quiet_soon: bool  # quiet time starts within the lead time
    temp: float | None  # measured zone temperature
    leads: bool
    last_written: float | None  # what Thermocast wrote last (None = nothing yet)
    writes_today: int
    window_open: bool = False  # a window of the zone is open: hold the base, never charge


def bt_decide(inp: BtInput) -> tuple[float | None, str]:
    """(target to write or None, reason code)."""
    if inp.temp is None:
        return None, "no_temperature"
    if inp.quiet_now or inp.quiet_soon:
        target = round_target(inp.base)
        cold = inp.quiet_now and inp.leads and inp.temp < inp.base - COLD_MARGIN
        settled = inp.last_written is not None and abs(inp.last_written - target) < STEP / 2
        # in quiet time only the base itself is written – once (a missed lead window must not leave the
        # room at its upper bound all night), or again for a cold leading zone
        if inp.quiet_now and settled and not cold:
            return None, "quiet"
    elif inp.window_open:
        target = round_target(inp.base)
    else:
        target = round_target(inp.high if inp.block_on and not inp.failsafe else inp.floor_now)
    if inp.last_written is not None and abs(inp.last_written - target) < STEP / 2:
        return None, "unchanged"
    if inp.writes_today >= MAX_WRITES_PER_DAY:
        return None, "budget"
    return target, "write"
