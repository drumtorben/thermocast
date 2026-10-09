"""Roll the real controller forward: plan hourly, apply actuator rules, simulate.

Shows the block sequence the controller would run if the forecast came true.
Step 0 uses exactly the inputs of the live ``plan()`` call, so it *is* the live decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace

from .planner import CostFn, PlanResult, ZonePlanInput, default_cost, plan
from .rules import Rules, apply_rules

DEFAULT_BLOCK_LENGTHS: tuple[int, ...] = (2, 3, 4, 6, 8, 10, 12)


def window(zi: ZonePlanInput, start: int, n: int | None = None) -> dict:
    """Per-hour lists of a zone input cut to [start, start + n) – for ``replace(zi, **window(...))``."""
    end = None if n is None else start + n
    return {
        "future": zi.future[start:end],
        "comfort_low": zi.comfort_low[start:end],
        "comfort_high": zi.comfort_high[start:end],
        "charge_cap": zi.charge_cap[start:end],
    }


def block_lengths_for(min_block_h: float) -> tuple[int, ...]:
    """Candidate lengths not shorter than the minimum block (same rule as the live planner)."""
    return tuple(n for n in DEFAULT_BLOCK_LENGTHS if n >= min_block_h) or (int(min_block_h),)


@dataclass
class ActuatorState:
    on: bool
    since_h: float = math.inf  # hours since the last switch
    switches_today: int = 0


@dataclass
class ZoneTrajectory:
    temp0: float
    mean: list[float] = field(default_factory=list)  # temperature at the end of step h
    std: list[float] = field(default_factory=list)
    contrib: list[dict[str, float]] = field(default_factory=list)  # change during step h by cause
    free_mean: list[float] = field(default_factory=list)  # never heating
    free_std: list[float] = field(default_factory=list)


@dataclass
class RolloutResult:
    first: PlanResult
    on: list[bool]
    zones: dict[str, ZoneTrajectory]

    @property
    def blocks(self) -> list[tuple[int, int]]:
        """[start, end) step indices of contiguous heating."""
        out: list[tuple[int, int]] = []
        start: int | None = None
        for i, on in enumerate([*self.on, False]):
            if on and start is None:
                start = i
            elif not on and start is not None:
                out.append((start, i))
                start = None
        return out


def rollout(
    zones: list[ZonePlanInput],
    steps: int,
    rules: Rules,
    state: ActuatorState,
    day_index: list[int] | None = None,
    lookahead: int = 24,
    block_lengths: tuple[int, ...] = DEFAULT_BLOCK_LENGTHS,
    z: float = 1.0,
    cost_fn: CostFn = default_cost,
) -> RolloutResult:
    """Simulate ``steps`` hours. ``zones[i].future``/``comfort_low`` start at the current hour and
    should reach ``steps + lookahead`` hours; shorter forecasts shorten the rollout.
    ``day_index[h]`` identifies the local day of step h (daily switch budget)."""
    if not zones:
        raise ValueError("rollout needs at least one zone")
    available = min(len(zi.future) for zi in zones)
    steps = min(steps, available)
    if steps < 1:
        raise ValueError("no forecast inputs")
    days = day_index or [0] * steps

    temps = {zi.name: zi.temp_now for zi in zones}
    var = {zi.name: zi.var0 for zi in zones}  # model variance
    wsd = {zi.name: zi.w0 for zi in zones}  # weather σ share (adds up linearly, see OnlineZoneModel.weather_std)
    hist = {zi.name: list(zi.model.history if zi.history is None else zi.history) for zi in zones}
    trajs = {zi.name: ZoneTrajectory(temp0=zi.temp_now) for zi in zones}
    on, since, switches = state.on, state.since_h, state.switches_today
    first: PlanResult | None = None
    on_list: list[bool] = []

    for h in range(steps):
        if h > 0 and days[h] != days[h - 1]:
            switches = 0
        forced = (on and since < rules.min_block_h) or (not on and since < rules.min_pause_h)
        if h == 0 or not forced:
            n = min(lookahead, available - h)
            inputs = [
                replace(
                    zi, temp_now=temps[zi.name], **window(zi, h, n),
                    var0=var[zi.name], w0=wsd[zi.name], history=hist[zi.name], scale_offset=h,
                )
                for zi in zones
            ]
            res = plan(inputs, block_lengths=block_lengths, z=z, cost_fn=cost_fn, shortcut=h > 0, running=on)
            if h == 0:
                first = res
            want = res.heat_now
            next_on = None if want else res.best.start
        else:
            want, next_on = on, None  # the rules decide anyway
        target, _ = apply_rules(want, on, since, switches, rules, next_on)
        if target != on:
            switches += 1
            since = 0.0
            on = target
        on_list.append(on)

        for zi in zones:
            name = zi.name
            cap = zi.cap_at(h)
            heats = on and (cap is None or temps[name] < cap)  # the thermostat closes at its cap
            rec = replace(zi.future[h], q=zi.q_on if heats else 0.0)
            p = zi.model.predict(temps[name], [rec], var0=var[name], history=hist[name], w0=wsd[name])
            tr = trajs[name]
            tr.mean.append(p.mean[0])
            tr.std.append(p.std[0])
            tr.contrib.append(p.contrib[0])
            hist[name] = [*hist[name], replace(rec, temp=temps[name])][-max(zi.model.spec.max_lag, 1) :]
            wsd[name] = p.weather[0]
            var[name] = max(p.std[0] ** 2 - p.weather[0] ** 2, 0.0)
            temps[name] = p.mean[0]
        since += 1.0

    for zi in zones:
        free = zi.predict(zi.future[:steps])
        trajs[zi.name].free_mean = free.mean
        trajs[zi.name].free_std = free.std
    assert first is not None
    return RolloutResult(first=first, on=on_list, zones=trajs)
