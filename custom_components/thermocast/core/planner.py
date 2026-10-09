"""Heating block planner: brute-force search over block start × length.

Heating "blocks" exploit the thermal mass of the screed: an oversized boiler runs
long and rarely instead of short-cycling. The cost function is pluggable so that
later a heat pump can optimise for COP / electricity price / PV surplus.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

import numpy as np

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
    var0: float = 0.0  # model variance of temp_now (rollout)
    history: list[HourRecord] | None = None  # lag history override (rollout)
    w0: float = 0.0  # weather σ share of temp_now (rollout)
    std_scale: tuple[float, ...] = ()  # calibrated σ factor per hour ahead of *now* (index 0 = 1 h)
    scale_offset: int = 0  # hours between now and future[0] (rollout re-plans)
    comfort_high: list[float | None] = field(default_factory=list)  # upper bound per hour, empty = none
    # charge cap per hour: a thermostat closes the valve once the room reaches it (empty/None = uncapped)
    charge_cap: list[float | None] = field(default_factory=list)

    def predict(self, records: list[HourRecord]) -> Prediction:
        # the planner only scores mean/σ – the cause split is computed where it is shown (rollout, hindcast)
        pred = self.model.predict(
            self.temp_now, records, var0=self.var0, history=self.history, with_contrib=False, w0=self.w0
        )
        if self.std_scale:
            last = len(self.std_scale) - 1
            pred.std = [s * self.std_scale[min(self.scale_offset + i, last)] for i, s in enumerate(pred.std)]
        return pred

    def cap_at(self, i: int) -> float | None:
        return self.charge_cap[i] if i < len(self.charge_cap) else None

    @property
    def capped(self) -> bool:
        return any(c is not None for c in self.charge_cap)

    def closed_hours(self, cand: Candidate, recs: list[HourRecord]) -> int:
        """Block hours in which this zone's thermostat holds the valve shut (the room is at its cap)."""
        if not self.capped:
            return 0
        return sum(1 for i, rec in enumerate(recs) if cand.active(i) and self.cap_at(i) is not None and rec.q == 0.0)

    def heating_records(self, cand: Candidate) -> list[HourRecord]:
        """Forecast inputs with the block's heating; capped hours (room at its cap) get no heat."""
        return self.heating_prediction(cand)[0]

    def heating_prediction(self, cand: Candidate) -> tuple[list[HourRecord], Prediction]:
        """(inputs, prediction) with the block's heating; a capped zone's thermostat closes at its cap."""
        q, mean, std = self.heating_batch([cand])
        recs = [replace(rec, q=float(q[0, i])) for i, rec in enumerate(self.future)]
        return recs, Prediction(mean=mean[0].tolist(), std=std[0].tolist())

    def predict_batch(self, q: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mean, std) per heating schedule (rows of ``q``), σ calibrated like ``predict``."""
        mean, std = self.model.predict_batch(
            self.temp_now, self.future, q, var0=self.var0, history=self.history, w0=self.w0
        )
        if self.std_scale:
            last = len(self.std_scale) - 1
            std = std * np.array([self.std_scale[min(self.scale_offset + i, last)] for i in range(q.shape[1])])
        return mean, std

    def heating_batch(self, cands: list[Candidate]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(q, mean, std) for many blocks at once (candidates × hours); a capped zone's thermostat closes the
        valve in hours that start at or above its cap."""
        n = len(self.future)
        q = np.where(_active(cands, n), self.q_on, 0.0)
        mean, std = self.predict_batch(q)
        if not self.capped or not n:
            return q, mean, std
        caps = np.array([np.nan if (c := self.cap_at(i)) is None else c for i in range(n)])
        for _ in range(3):  # closing the valve changes the later hours – a few passes settle it
            start_temp = np.concatenate([np.full((len(cands), 1), self.temp_now), mean[:, :-1]], axis=1)
            shut = (q > 0) & (start_temp >= caps)  # NaN (no cap) compares False
            rows = shut.any(axis=1)
            if not rows.any():
                break
            q[shut] = 0.0
            mean[rows], std[rows] = self.predict_batch(q[rows])
        return q, mean, std


@dataclass
class Candidate:
    start: int | None  # hour offset, None = no block
    length: int = 0
    continues: bool = False  # starts now while a block is already running: no new burner start

    def active(self, hour: int) -> bool:
        return self.start is not None and self.start <= hour < self.start + self.length


def _active(cands: list[Candidate], n_hours: int) -> np.ndarray:
    """Block hours as a mask (candidates × hours), like ``Candidate.active``."""
    hours = np.arange(n_hours)
    start = np.array([-1 if c.start is None else c.start for c in cands])[:, None]
    end = np.array([-1 if c.start is None else c.start + c.length for c in cands])[:, None]
    return (hours >= start) & (hours < end)


@dataclass
class ScoredCandidate:
    candidate: Candidate
    cost: float
    parts: dict[str, float]
    violation: float  # K·h below comfort (leading zones, lower bound)
    preds: dict[str, Prediction]  # leading zones with this candidate
    covers: bool = False  # a block that leaves no violation inside the horizon after it (nothing for a next block)


def _rank(s: ScoredCandidate) -> tuple[float, int]:
    """Cheapest first. On a tie, among blocks that cover the rest of the horizon the later start wins (the heat
    is not lost before it is needed) – typically when every candidate's coast reaches the cap. Other ties keep
    the search order (early first): a short block before a violation it leaves to the next block would, the
    later it starts, leave that next block no time."""
    return round(s.cost, 6), -(s.candidate.start or 0) if s.covers else 0


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


# (candidate, K·h too cold, K·h too warm, coast hours after the block until the next block is needed,
#  block hours with the thermostat-controlled rooms closed – as a share of those rooms)
CostFn = Callable[[Candidate, float, float, float, float], dict[str, float]]
MIN_COAST_H = 2  # a "next block" sooner than this is no real pause – its violations stay with this candidate
# with few open radiators the boiler only fires briefly and waits out its anti-cycling lock (≈ 45 min)
CYCLE_STARTS_PER_H = 60.0 / 45.0


def default_cost(
    candidate: Candidate, comfort_violation: float, overheat: float = 0.0, coast_h: float = 0.0,
    closed_h: float = 0.0,
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

    A start costs 1…10, a block hour 0.5…0.15 – both **per day**: scaled by 24 h / (hours from now until the
    next block is needed = start + block + coast), so a block that stores enough heat for a long pause pays for
    itself. Counted from now, not from the block start: an early block buys no pause the house would have had
    anyway – with the same next need the later block wins, and a later block usually lasts longer (the heat is
    not lost before it is needed). Staying below the lower bound costs
    20 per K·h (leading zones), going above the upper bound 4 per K·h (all zones). Block hours in which the
    thermostat-controlled rooms are closed (at their cap, e.g. in quiet time) cost extra burner starts: few
    consumers make the boiler cycle inside the block. Marked with ``coast = True``: the planner leaves
    violations after the coast to the next block.
    """
    w = min(1.0, max(0.0, weight))

    def cost(
        candidate: Candidate, comfort_violation: float, overheat: float, coast_h: float, closed_h: float = 0.0
    ) -> dict[str, float]:
        parts = {"comfort": 20.0 * comfort_violation, "overheat": 4.0 * overheat, "start": 0.0, "cycling": 0.0,
                 "energy": 0.0, "delay": 0.0}
        if candidate.start is not None:
            per_day = 24.0 / max(1.0, candidate.start + candidate.length + coast_h)
            parts["start"] = 0.0 if candidate.continues else (1.0 + 9.0 * w) * per_day
            parts["cycling"] = (1.0 + 9.0 * w) * per_day * CYCLE_STARTS_PER_H * closed_h
            parts["energy"] = (0.5 - 0.35 * w) * candidate.length * per_day
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


def _coast_beyond(preds: dict[str, Prediction], zones: list[ZonePlanInput], z: float, cap_h: float = 48.0) -> float:
    """Hours the stored heat still lasts after the horizon: (end temperature − lower bound) / cooling rate
    of the last 3 h, the shortest over the leading zones. Lets the planner value a longer charge
    that already covers the whole horizon. The cap is generous: at 24 h every block that covers the
    horizon hit it, all start times cost the same and the later block lost its advantage."""
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
    consumers = [zz for zz in zones if zz.capped]  # rooms whose thermostat the planner sets
    scored_names = {zz.name for zz in scored_zones}
    scored_zones += [zz for zz in consumers if zz.name not in scored_names]
    bounded_names = {zz.name for zz in bounded}
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

    # all blocks of a zone in one go (candidates[0] is "no block": the free run)
    blocks = candidates[1:]
    batch: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    closed_by_cand = np.zeros(len(blocks))
    for zz in scored_zones:
        if not blocks:
            break
        q, mean, std = zz.heating_batch(blocks)
        batch[zz.name] = (mean, std)
        if zz.capped:  # block hours with the valve shut (the room at its cap), as a share of those rooms
            capped_hour = np.array([zz.cap_at(i) is not None for i in range(q.shape[1])])
            closed_by_cand += (_active(blocks, q.shape[1]) & capped_hour & (q == 0.0)).sum(axis=1) / len(consumers)

    scored_all: list[ScoredCandidate] = []
    for k, cand in enumerate(candidates):
        preds: dict[str, Prediction] = {}
        viol: list[tuple[int, float]] = []
        overheat = 0.0
        closed = float(closed_by_cand[k - 1]) if cand.start is not None else 0.0
        for zz in scored_zones:
            if cand.start is None:
                pred = free[zz.name]
            else:
                mean, std = batch[zz.name]
                pred = Prediction(mean=mean[k - 1].tolist(), std=std[k - 1].tolist())
            if zz.leads_release:
                preds[zz.name] = pred
                viol += violations(pred, zz.comfort_low, z)
            if zz.name in bounded_names:
                overheat += overheat_kh(pred, zz.comfort_high)
        violation, coast = _counted(viol, cand, horizon, coast_mode)
        covers = cand.start is not None and not any(i >= cand.start + cand.length for i, _ in viol)
        if coast_mode and covers:
            coast += _coast_beyond(preds, leading, z)  # no next block needed inside the horizon
        parts = cost_fn(cand, violation, overheat, coast, closed)
        scored_all.append(ScoredCandidate(cand, sum(parts.values()), parts, violation, preds, covers=covers))

    ranked = sorted(scored_all, key=_rank)  # stable
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
