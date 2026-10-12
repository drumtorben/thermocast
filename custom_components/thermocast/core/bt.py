"""Which target to give a zone's Better Thermostat – a pure decision (no HA imports).

During a heating block the room may charge up to its charge target (the upper bound, or only the base
temperature when comfort is far away or about to end); otherwise it is held at its lower bound (comfort − band in comfort
time, the base temperature outside). Quiet time: the thermostat is switched off shortly before (Better
Thermostat keeps sending the room temperature to the TRV, and a regulating TRV answers that with valve
noise; an off TRV stays shut and quiet), then nothing is written – unless the room gets cold (base − 1 K: on
at the base until the base is reached again) or the temperature is unknown. ``hand_back`` (control off,
unload, update error) always switches back on.
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
    high: float  # charge target in a block (upper bound, or the lower bound when comfort is far away or ends soon)
    base: float  # base temperature (quiet time)
    quiet_now: bool
    quiet_soon: bool  # quiet time starts within the lead time
    temp: float | None  # measured zone temperature
    last_written: float | None  # what Thermocast wrote last (None = nothing yet)
    writes_today: int
    window_open: bool = False  # a window of the zone is open: hold the base, never charge
    last_off: bool = False  # Thermocast switched the thermostat off (quiet time)
    hand_back: bool = False  # control off / unload / update error: thermostat on, at its lower bound


def bt_decide(inp: BtInput) -> tuple[float | None, str]:
    """(target to write or None, reason code). Reason ``off``: switch the thermostat off (target None);
    any target means: on (again) at that target."""
    quiet = inp.quiet_now or inp.quiet_soon
    if quiet and not inp.hand_back and inp.temp is not None:
        # off ↔ on with hysteresis: off from the base up, on again below base − 1 K
        warm = inp.temp >= inp.base - (COLD_MARGIN if inp.last_off else 0.0)
        if warm and inp.last_off:
            return None, "quiet"
        if warm:
            return (None, "budget") if inp.writes_today >= MAX_WRITES_PER_DAY else (None, "off")
    if inp.temp is None and not inp.last_off and not inp.hand_back:
        return None, "no_temperature"
    if quiet or inp.window_open:
        target = round_target(inp.base)
    else:
        target = round_target(inp.high if inp.block_on and not inp.failsafe else inp.floor_now)
    if not inp.last_off and inp.last_written is not None and abs(inp.last_written - target) < STEP / 2:
        return None, "unchanged"
    if not inp.last_off and inp.writes_today >= MAX_WRITES_PER_DAY:
        return None, "budget"
    return target, "write"
