"""Heating block planner: brute-force search over block start × length.

Heating "blocks" exploit the thermal mass of the screed: an oversized boiler runs
long and rarely instead of short-cycling. The cost function is pluggable so that
later a heat pump can optimise for COP / electricity price / PV surplus.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from .model import HourRecord, OnlineZoneModel, Prediction


@dataclass
class ZonePlanInput:
    name: str  # stable id (subentry id in Home Assistant)
    model: OnlineZoneModel
    temp_now: float
    future: list[HourRecord]  # forecast inputs with q = 0
    comfort_low: list[float | None]  # lower comfort bound per hour, None = don't care
    q_on: float = 10.0  # typical heating proxy while a block runs
    leads_release: bool = True
    var0: float = 0.0  # variance of temp_now (rollout)
    history: list[HourRecord] | None = None  # lag history override (rollout)
    std_scale: tuple[float, ...] = ()  # calibrated σ factor per hour ahead of *now* (index 0 = 1 h)
    scale_offset: int = 0  # hours between now and future[0] (rollout re-plans)
    comfort_high: list[float | None] = field(default_factory=list)  # upper bound per hour, empty = none
    # charge cap per hour: a thermostat closes the valve once the room reaches it (empty/None = uncapped)
    charge_cap: list[float | None] = field(default_factory=list)

    def predict(self, records: list[HourRecord]) -> Prediction:
        # the planner only scores mean/σ – the cause split is computed where it is shown (rollout, hindcast)
        pred = self.model.predict(self.temp_now, records, var0=self.var0, history=self.history, with_contrib=False)
        if self.std_scale:
            last = len(self.std_scale) - 1
            pred.std = [s * self.std_scale[min(self.scale_offset + i, last)] for i, s in enumerate(pred.std)]
        return pred

    def cap_at(self, i: int) -> float | None:
        return self.charge_cap[i] if i < len(self.charge_cap) else None

    @property
    def capped(self) -> bool:
        return any(c is not None for c in self.charge_cap)

    def heating_records(self, cand: Candidate) -> list[HourRecord]:
        """Forecast inputs with the block's heating; capped hours (room at its cap) get no heat."""
        return self.heating_prediction(cand)[0]

    def heating_prediction(self, cand: Candidate) -> tuple[list[HourRecord], Prediction]:
        """(inputs, prediction) with the block's heating; a capped zone's thermostat closes at its cap."""
        recs = [replace(rec, q=self.q_on if cand.active(i) else 0.0) for i, rec in enumerate(self.future)]
        pred = self.predict(recs)
        if not self.capped or cand.start is None:
            return recs, pred
        for _ in range(3):  # closing the valve changes the later hours – a few passes settle it
            changed = False
            for i in range(len(recs)):
                start_temp = self.temp_now if i == 0 else pred.mean[i - 1]
                cap = self.cap_at(i)
                if recs[i].q > 0 and cap is not None and start_temp >= cap:
                    recs[i] = replace(recs[i], q=0.0)
                    changed = True
            if not changed:
                break
            pred = self.predict(recs)
        return recs, pred


@dataclass
class Candidate:
    start: int | None  # hour offset, None = no block
    length: int = 0
    continues: bool = False  # starts now while a block is already running: no new burner start

    def active(self, hour: int) -> bool:
        return self.start is not None and self.start <= hour < self.start + self.length


@dataclass
class ScoredCandidate:
    candidate: Candidate
    cost: float
    parts: dict[str, float]
    violation: float  # K·h below comfort (leading zones, lower bound)
    preds: dict[str, Prediction]  # leading zones with this candidate


@dataclass
class PlanResult:
    best: Candidate
    cost: float
    free_run: dict[str, Prediction] = field(default_factory=dict)  # without heating
    planned: dict[str, Prediction] = field(default_factory=dict)  # all zones with the chosen block
    violation_free: dict[str, float] = field(default_factory=dict)
    ranked: list[ScoredCandidate] = field(default_factory=list)  # cheapest first

    @property
    def heat_now(self) -> bool:
        return self.best.active(0)


# (candidate, K·h too cold, K·h too warm, coast hours after the block until the next block is needed)
CostFn = Callable[[Candidate, float, float, float], dict[str, float]]
MIN_COAST_H = 2  # a "next block" sooner than this is no real pause – its violations stay with this candidate


def default_cost(
    candidate: Candidate, comfort_violation: float, overheat: float = 0.0, coast_h: float = 0.0
) -> dict[str, float]:
    """Gas boiler: comfort first, then few starts, then little energy (ignores upper bound and coast)."""
    parts = {"comfort": 20.0 * comfort_violation, "start": 0.0, "energy": 0.0, "delay": 0.0}
    if candidate.start is not None:
        parts["start"] = 1.0  # one burner start-up phase
        parts["energy"] = 0.15 * candidate.length
        parts["delay"] = 0.01 * candidate.start  # prefer late starts slightly (less loss)
    return parts


def charge_cost(weight: float) -> CostFn:
    """Charge and coast: ``weight`` 0 = little gas (short blocks), 1 = few burner starts (long blocks).

    A start costs 1…10, a block hour 0.5…0.15 – both **per day**: scaled by 24 h / (block + coast), so a
    block that stores enough heat for a long pause pays for itself. Staying below the lower bound costs
    20 per K·h (leading zones), going above the upper bound 4 per K·h (all zones). Marked with
    ``coast = True``: the planner leaves violations after the coast to the next block.
    """
    w = min(1.0, max(0.0, weight))

    def cost(candidate: Candidate, comfort_violation: float, overheat: float, coast_h: float) -> dict[str, float]:
        parts = {"comfort": 20.0 * comfort_violation, "overheat": 4.0 * overheat, "start": 0.0, "energy": 0.0,
                 "delay": 0.0}
        if candidate.start is not None:
            per_day = 24.0 / max(1.0, candidate.length + coast_h)
            parts["start"] = 0.0 if candidate.continues else (1.0 + 9.0 * w) * per_day
            parts["energy"] = (0.5 - 0.35 * w) * candidate.length * per_day
            parts["delay"] = 0.01 * candidate.start
        return parts

    cost.coast = True  # type: ignore[attr-defined]
    return cost


def overheat_kh(pred: Prediction, comfort_high: list[float | None]) -> float:
    """K·h of the mean above the upper bound."""
    return sum(max(0.0, m - hi) for m, hi in zip(pred.mean, comfort_high) if hi is not None)


def violations(pred: Prediction, comfort_low: list[float | None], z: float) -> list[tuple[int, float]]:
    """(hour index, deficit K) where the lower bound falls below comfort."""
    return [
        (i, low - lb) for i, (low, lb) in enumerate(zip(comfort_low, pred.lower(z))) if low is not None and lb < low
    ]


def _violation(pred: Prediction, comfort_low: list[float | None], z: float) -> float:
    return sum(d for _, d in violations(pred, comfort_low, z))


def _counted(
    viol: list[tuple[int, float]], cand: Candidate, horizon: int, coast_mode: bool
) -> tuple[float, float]:
    """(K·h of violation this candidate is charged for, coast hours after its block).

    Coast = hours from the block end until the first violation after it (or the horizon end). In coast
    mode that later violation is the next block's job – unless the pause would be shorter than
    ``MIN_COAST_H``, then this candidate keeps it.
    """
    total = sum(d for _, d in viol)
    if cand.start is None:
        return total, 0.0
    end = cand.start + cand.length
    after = [i for i, _ in viol if i >= end]
    first = min(after) if after else horizon
    coast = float(max(0, first - end))
    if coast_mode and after and coast >= MIN_COAST_H:
        return sum(d for i, d in viol if i < first), coast
    return total, coast


def _coast_beyond(preds: dict[str, Prediction], zones: list[ZonePlanInput], z: float, cap_h: float = 24.0) -> float:
    """Hours the stored heat still lasts after the horizon: (end temperature − lower bound) / cooling rate
    of the last 3 h, the shortest over the leading zones. Lets the planner value a longer charge
    that already covers the whole horizon."""
    out = cap_h
    for zz in zones:
        pred = preds.get(zz.name)
        bounds = [b for b in zz.comfort_low if b is not None]
        if pred is None or len(pred.mean) < 4 or not bounds:
            continue
        lower = pred.lower(z)
        rate = max(0.02, (lower[-4] - lower[-1]) / 3.0)  # K/h, at least a slow drift
        floor = zz.comfort_low[-1] if zz.comfort_low[-1] is not None else min(bounds)
        out = min(out, max(0.0, (lower[-1] - floor) / rate))
    return out


def plan(
    zones: list[ZonePlanInput],
    block_lengths: tuple[int, ...] = (2, 3, 4, 6, 8, 10, 12),
    start_step: int = 1,
    z: float = 1.0,
    cost_fn: CostFn = default_cost,
    shortcut: bool = False,
    running: bool = False,
) -> PlanResult:
    """Choose the cheapest block. ``shortcut`` skips the search when no leading zone is violated
    without heating – valid for cost functions where an unneeded block never pays off (default_cost)."""
    leading = [zz for zz in zones if zz.leads_release]
    # the upper bound counts where the planner's heat lands: leading zones and zones whose thermostat it sets
    # (other rooms are limited by their own thermostats)
    bounded = [zz for zz in zones if (zz.leads_release or zz.capped) and any(h is not None for h in zz.comfort_high)]
    scored_zones = leading + [zz for zz in bounded if not zz.leads_release]
    horizon = min((len(zz.future) for zz in zones), default=0)

    free = {zz.name: zz.predict(zz.future) for zz in zones}
    free_violation = {zz.name: _violation(free[zz.name], zz.comfort_low, z) for zz in zones}
    free_overheat = sum(overheat_kh(free[zz.name], zz.comfort_high) for zz in bounded)

    coast_mode = bool(getattr(cost_fn, "coast", False))

    # a running charge may pay off without any violation in sight (longer coast) – no shortcut then
    if shortcut and not (running and coast_mode) and sum(free_violation[zz.name] for zz in leading) == 0.0:
        none = Candidate(None)
        parts = cost_fn(none, 0.0, free_overheat, 0.0)
        scored = ScoredCandidate(none, sum(parts.values()), parts, 0.0, {zz.name: free[zz.name] for zz in leading})
        return PlanResult(
            best=none, cost=scored.cost, free_run=free, planned=dict(free),
            violation_free=free_violation, ranked=[scored],
        )

    # a block starting after the first violation cannot prevent it – in coast mode those candidates only
    # cost time (the next re-plan covers later blocks); a running block with nothing in sight: continue or stop
    latest = horizon
    if coast_mode:
        first = [v[0][0] for zz in leading if (v := violations(free[zz.name], zz.comfort_low, z))]
        latest = min(first) + 1 if first else (1 if running else horizon)
    candidates = [Candidate(None)]
    for length in block_lengths:
        for start in range(0, min(max(horizon - length + 1, 0), latest), start_step):
            candidates.append(Candidate(start, length, continues=running and start == 0))

    scored_all: list[ScoredCandidate] = []
    for cand in candidates:
        preds: dict[str, Prediction] = {}
        viol: list[tuple[int, float]] = []
        overheat = 0.0
        for zz in scored_zones:
            pred = free[zz.name] if cand.start is None else zz.heating_prediction(cand)[1]
            if zz.leads_release:
                preds[zz.name] = pred
                viol += violations(pred, zz.comfort_low, z)
            overheat += overheat_kh(pred, zz.comfort_high)
        violation, coast = _counted(viol, cand, horizon, coast_mode)
        if coast_mode and cand.start is not None and not any(i >= cand.start + cand.length for i, _ in viol):
            coast += _coast_beyond(preds, leading, z)  # no next block needed inside the horizon
        parts = cost_fn(cand, violation, overheat, coast)
        scored_all.append(ScoredCandidate(cand, sum(parts.values()), parts, violation, preds))

    ranked = sorted(scored_all, key=lambda s: s.cost)  # stable: ties keep search order
    best = ranked[0]
    planned: dict[str, Prediction] = {}
    for zz in zones:
        if best.candidate.start is None:
            planned[zz.name] = free[zz.name]
        elif zz.name in best.preds:
            planned[zz.name] = best.preds[zz.name]
        else:
            planned[zz.name] = zz.heating_prediction(best.candidate)[1]
    return PlanResult(
        best=best.candidate, cost=best.cost, free_run=free, planned=planned,
        violation_free=free_violation, ranked=ranked,
    )
