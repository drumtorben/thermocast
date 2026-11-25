"""Burner starts inside a heating block, learned per block hour.

An oversized boiler whose minimum power exceeds what the house takes cycles inside a block: it fires, reaches
its switch-off difference, waits out its anti-cycling lock and fires again. How often depends on how many
consumers take heat – with the thermostat rooms shut (quiet time, at their cap) only the floor heating is left.

Model: extra starts per block hour = ``r0 + r1 · closed``, ``closed`` = share of the thermostat-controlled rooms
with their valve shut. Prior (no data): ``r0 = 0``, ``r1 = 60 / anti-cycling lock`` – a lone floor heating
fires once per lock, open radiators take the rest. Learned online with forgetting (RLS) from the burner starts
counter in block hours (not the block's first hour – its start is priced on its own –, no hot water charge).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

DEFAULT_LOCK_MIN = 45.0  # anti-cycling lock when the option is off (0)
FORGETTING = 0.99  # per sample: ~100 block hours (2–3 weeks of heating)
PRIOR_VAR = (0.05, 0.5)  # r0 is held near 0 until data says otherwise; r1 moves faster
NOISE_VAR = 1.0  # starts per hour are counts (~Poisson around 1)
MAX_RATE = 4.0  # starts per hour – a cap against a broken counter
BLOCK_SHARE = 0.75  # an hour counts as block hour from this space-heating share on (as q_on)
DHW_FREE = 0.02  # hot water share up to which the hour counts as without charge


def prior_rates(lock_min: float | None) -> tuple[float, float]:
    lock = lock_min if lock_min and lock_min > 0 else DEFAULT_LOCK_MIN
    return 0.0, min(MAX_RATE, 60.0 / lock)


@dataclass
class CycleRate:
    """Learned extra burner starts per block hour: ``r0 + r1 · closed``. Empty (``n == 0``) = prior."""

    theta: np.ndarray | None = None
    p: np.ndarray | None = None
    n: int = 0
    lam: float = FORGETTING
    last: dict[str, Any] = field(default_factory=dict)  # the last sample (diagnostics / panel)

    def rates(self, lock_min: float | None) -> tuple[float, float]:
        if self.n == 0 or self.theta is None:
            return prior_rates(lock_min)
        return float(self.theta[0]), float(self.theta[1])

    def update(self, closed: float, starts: float, lock_min: float | None) -> None:
        """One block hour: ``closed`` 0..1, ``starts`` burner starts in that hour."""
        if not (0.0 <= closed <= 1.0) or not (0.0 <= starts <= MAX_RATE * 2):
            return
        if self.n == 0 or self.theta is None or self.p is None:
            self.theta = np.array(prior_rates(lock_min), dtype=float)
            self.p = np.diag(PRIOR_VAR).astype(float) / NOISE_VAR
        x = np.array([1.0, closed])
        px = self.p @ x
        gain = px / (self.lam + x @ px)
        self.theta = self.theta + gain * (starts - x @ self.theta)
        self.p = (self.p - np.outer(gain, px)) / self.lam
        # no wind-up when the same closed share repeats for weeks: never less certain than the prior
        for i, v in enumerate(PRIOR_VAR):
            if self.p[i, i] > v / NOISE_VAR:
                scale = np.sqrt(v / NOISE_VAR / self.p[i, i])
                self.p[i, :] *= scale
                self.p[:, i] *= scale
        r0 = min(MAX_RATE, max(0.0, float(self.theta[0])))
        r1 = min(MAX_RATE - r0, max(0.0, float(self.theta[1])))
        self.theta = np.array([r0, r1])
        self.n += 1
        self.last = {"closed": round(closed, 2), "starts": starts}

    def to_dict(self) -> dict[str, Any]:
        if self.theta is None or self.p is None:
            return {"n": 0}
        return {"theta": self.theta.tolist(), "p": self.p.tolist(), "n": self.n, "last": self.last}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> CycleRate:
        if not d or not d.get("n") or d.get("theta") is None or d.get("p") is None:
            return cls()
        try:
            return cls(theta=np.array(d["theta"], dtype=float), p=np.array(d["p"], dtype=float), n=int(d["n"]),
                       last=dict(d.get("last") or {}))
        except (TypeError, ValueError):
            return cls()


def cycle_sample(
    heat: float | None, heat_before: float | None, dhw: float | None, closed: float | None, starts: float | None
) -> tuple[float, float] | None:
    """(closed, starts) if the hour is a learnable block hour, else None.

    ``heat``/``heat_before``: space-heating share of this and the previous hour; ``dhw``: hot water share (None =
    no hot water entity); ``closed``: share of the thermostat rooms shut (0 without such rooms); ``starts``: counter
    increase in the hour.
    """
    if heat is None or heat_before is None or closed is None or starts is None:
        return None
    if heat < BLOCK_SHARE or heat_before < BLOCK_SHARE:
        return None
    if dhw is not None and dhw > DHW_FREE:
        return None
    return closed, starts


def closed_share(valves: list[float | None]) -> float | None:
    """Share of thermostat rooms with the valve shut, from their valve shares (1 = open all hour)."""
    if not valves:
        return 0.0
    if any(v is None for v in valves):
        return None
    return sum(1.0 - min(1.0, max(0.0, v)) for v in valves) / len(valves)  # type: ignore[arg-type]


def learn(rate: CycleRate, samples: list[tuple[float, float]], lock_min: float | None) -> CycleRate:
    for closed, starts in samples:
        rate.update(closed, starts, lock_min)
    return rate
