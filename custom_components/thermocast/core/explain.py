"""Why the planner decided what it did – as codes and numbers (texts live in the frontend)."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from .planner import PlanResult, ZonePlanInput, violations

HOUR = timedelta(hours=1)


def _iso(t: datetime | None) -> str | None:
    return t.isoformat() if t else None


def explain(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime, z: float) -> dict:
    """``hour0`` = start of the current hour; prediction index i refers to hour0 + (i + 1) h."""
    leading = [zz for zz in zones if zz.leads_release]
    driver: str | None = None
    first_i: int | None = None
    deficit = 0.0
    for zz in leading:
        viol = violations(first.free_run[zz.name], zz.comfort_low, z)
        if viol and (first_i is None or viol[0][0] < first_i):
            driver, first_i, deficit = zz.name, viol[0][0], max(d for _, d in viol)
    out: dict = {
        "code": None,
        "driver": driver,
        "first_violation": _iso(hour0 + (first_i + 1) * HOUR) if first_i is not None else None,
        "deficit_k": round(deficit, 2) if driver else None,
        "next_block": None,
        "lead_h": None,
    }
    best = first.best
    if best.start is not None:
        out["next_block"] = {
            "start": _iso(hour0 + best.start * HOUR),
            "end": _iso(hour0 + (best.start + best.length) * HOUR),
        }
        out["code"] = "heating_now" if first.heat_now else "block_planned"
        if first_i is not None:
            out["lead_h"] = first_i + 1 - best.start
        return out
    if driver is not None:
        out["code"] = "violation_accepted"
        return out
    sunless = any(
        violations(zz.predict([replace(r, irr={}) for r in zz.future]), zz.comfort_low, z) for zz in leading
    )
    out["code"] = "no_need_sun" if sunless else "no_need"
    return out


def robustness(res: PlanResult, res_z0: PlanResult, close_below: float = 1.0) -> dict:
    """``close`` if the runner-up costs less than one extra burner start more."""
    margin = res.ranked[1].cost - res.ranked[0].cost if len(res.ranked) > 1 else None
    return {
        "margin": round(margin, 3) if margin is not None else None,
        "level": "close" if margin is not None and margin < close_below else "clear",
        "sigma_driven": res.best.start is not None and res_z0.best.start is None,
    }


@dataclass
class PlanSnapshot:
    hour0: datetime
    next_block: tuple[datetime, datetime] | None
    t_out: dict[datetime, float]
    irr: dict[datetime, float]  # summed over surfaces, W/m²
    expected_next: dict[str, float]  # zone -> expected temperature at hour0 + 1 h


def snapshot_from(first: PlanResult, zones: list[ZonePlanInput], hour0: datetime) -> PlanSnapshot:
    best = first.best
    block = None
    if best.start is not None:
        block = (hour0 + best.start * HOUR, hour0 + (best.start + best.length) * HOUR)
    ref = zones[0].future if zones else []
    return PlanSnapshot(
        hour0=hour0,
        next_block=block,
        t_out={hour0 + i * HOUR: r.t_out for i, r in enumerate(ref)},
        irr={hour0 + i * HOUR: sum(r.irr.values()) for i, r in enumerate(ref)},
        expected_next={zz.name: first.planned[zz.name].mean[0] for zz in zones if first.planned[zz.name].mean},
    )


def _block_dict(block: tuple[datetime, datetime] | None) -> dict | None:
    return {"start": _iso(block[0]), "end": _iso(block[1])} if block else None


def _changed(a: tuple[datetime, datetime] | None, b: tuple[datetime, datetime] | None) -> bool:
    if a is None or b is None:
        return (a is None) != (b is None)
    return abs(a[0] - b[0]) >= HOUR or abs(a[1] - b[1]) >= HOUR


def plan_change(prev: PlanSnapshot | None, cur: PlanSnapshot, temps_now: dict[str, float]) -> dict | None:
    """Report a shifted/new/dropped next block and its most likely cause (largest input deviation)."""
    if prev is None or not _changed(prev.next_block, cur.next_block):
        return None
    ends = [b[1] for b in (prev.next_block, cur.next_block) if b]
    until = max(ends) if ends else cur.hour0 + 24 * HOUR
    best: tuple[float, dict] | None = None

    def consider(score: float, cause: dict) -> None:
        nonlocal best
        if best is None or score > best[0]:
            best = (score, cause)

    for h in sorted(cur.t_out):
        if cur.hour0 <= h < until and h in prev.t_out:
            d = round(cur.t_out[h] - prev.t_out[h], 2)
            consider(abs(d) / 1.0, {"kind": "t_out", "delta": d, "at": _iso(h), "zone": None})
            if h in prev.irr and h in cur.irr:
                di = round(cur.irr[h] - prev.irr[h], 0)
                consider(abs(di) / 100.0, {"kind": "sun", "delta": di, "at": _iso(h), "zone": None})
    if prev.hour0 + HOUR == cur.hour0:
        for zone, expected in prev.expected_next.items():
            if zone in temps_now:
                d = round(temps_now[zone] - expected, 2)
                consider(abs(d) / 0.2, {"kind": "room", "delta": d, "at": None, "zone": zone})
    return {
        "previous_block": _block_dict(prev.next_block),
        "current_block": _block_dict(cur.next_block),
        "cause": best[1] if best and best[0] >= 0.5 else None,
    }
