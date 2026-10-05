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

    def predict(self, records: list[HourRecord]) -> Prediction:
        pred = self.model.predict(self.temp_now, records, var0=self.var0, history=self.history)
        if self.std_scale:
            last = len(self.std_scale) - 1
            pred.std = [s * self.std_scale[min(self.scale_offset + i, last)] for i, s in enumerate(pred.std)]
        return pred


@dataclass
class Candidate:
    start: int | None  # hour offset, None = no block
    length: int = 0

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


CostFn = Callable[[Candidate, float], dict[str, float]]


def default_cost(candidate: Candidate, comfort_violation: float) -> dict[str, float]:
    """Gas boiler: comfort first, then few starts, then little energy."""
    parts = {"comfort": 20.0 * comfort_violation, "start": 0.0, "energy": 0.0, "delay": 0.0}
    if candidate.start is not None:
        parts["start"] = 1.0  # one burner start-up phase
        parts["energy"] = 0.15 * candidate.length
        parts["delay"] = 0.01 * candidate.start  # prefer late starts slightly (less loss)
    return parts


def violations(pred: Prediction, comfort_low: list[float | None], z: float) -> list[tuple[int, float]]:
    """(hour index, deficit K) where the lower bound falls below comfort."""
    return [
        (i, low - lb) for i, (low, lb) in enumerate(zip(comfort_low, pred.lower(z))) if low is not None and lb < low
    ]


def _violation(pred: Prediction, comfort_low: list[float | None], z: float) -> float:
    return sum(d for _, d in violations(pred, comfort_low, z))


def _with_block(zone: ZonePlanInput, cand: Candidate) -> list[HourRecord]:
    return [replace(rec, q=zone.q_on if cand.active(i) else 0.0) for i, rec in enumerate(zone.future)]


def plan(
    zones: list[ZonePlanInput],
    block_lengths: tuple[int, ...] = (2, 3, 4, 6, 8),
    start_step: int = 1,
    z: float = 1.0,
    cost_fn: CostFn = default_cost,
    shortcut: bool = False,
) -> PlanResult:
    """Choose the cheapest block. ``shortcut`` skips the search when no leading zone is violated
    without heating – valid for cost functions where an unneeded block never pays off (default_cost)."""
    leading = [zz for zz in zones if zz.leads_release]
    horizon = min((len(zz.future) for zz in zones), default=0)

    free = {zz.name: zz.predict(zz.future) for zz in zones}
    free_violation = {zz.name: _violation(free[zz.name], zz.comfort_low, z) for zz in zones}

    if shortcut and sum(free_violation[zz.name] for zz in leading) == 0.0:
        none = Candidate(None)
        parts = cost_fn(none, 0.0)
        scored = ScoredCandidate(none, sum(parts.values()), parts, 0.0, {zz.name: free[zz.name] for zz in leading})
        return PlanResult(
            best=none, cost=scored.cost, free_run=free, planned=dict(free),
            violation_free=free_violation, ranked=[scored],
        )

    candidates = [Candidate(None)]
    for length in block_lengths:
        for start in range(0, max(horizon - length + 1, 0), start_step):
            candidates.append(Candidate(start, length))

    scored_all: list[ScoredCandidate] = []
    for cand in candidates:
        preds: dict[str, Prediction] = {}
        violation = 0.0
        for zz in leading:
            pred = free[zz.name] if cand.start is None else zz.predict(_with_block(zz, cand))
            preds[zz.name] = pred
            violation += _violation(pred, zz.comfort_low, z)
        parts = cost_fn(cand, violation)
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
            planned[zz.name] = zz.predict(_with_block(zz, best.candidate))
    return PlanResult(
        best=best.candidate, cost=best.cost, free_run=free, planned=planned,
        violation_free=free_violation, ranked=ranked,
    )
