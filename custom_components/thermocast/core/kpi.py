"""Daily KPIs from hourly statistics: burner starts, heating energy, degree days, comfort (no HA imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, tzinfo
from statistics import fmean
from typing import Any

HDD_MIN = 1.0  # K·d – below that, kWh per degree day is not meaningful


@dataclass
class ZoneKpiInput:
    """Hourly statistics of one leading zone (aggregated over its sensors), keyed by UTC hour start."""

    hourly_min: dict[datetime, float] = field(default_factory=dict)
    hourly_mean: dict[datetime, float] = field(default_factory=dict)
    comfort: dict[datetime, float | None] = field(default_factory=dict)  # lower comfort bound per hour


def _r(x: float | None, nd: int = 2) -> float | None:
    return None if x is None else round(x, nd)


def _by_day(values: dict[datetime, float], tz: tzinfo) -> dict[date, list[float]]:
    out: dict[date, list[float]] = {}
    for t, v in values.items():
        out.setdefault(t.astimezone(tz).date(), []).append(v)
    return out


def daily_kpis(
    *,
    days: list[date],
    tz: tzinfo,
    today: date,
    starts: dict[datetime, float] | None,
    energy: dict[datetime, float] | None,
    t_out: dict[datetime, float] | None,
    zones: list[ZoneKpiInput],
    hdd_base: float = 15.0,
) -> list[dict[str, Any]]:
    """One row per local day. Counter changes < 0 (resets) count as 0. Outdoor mean needs ≥ 18 hours."""
    starts_d = _by_day(starts or {}, tz)
    energy_d = _by_day(energy or {}, tz)
    t_out_d = _by_day(t_out or {}, tz)
    rows: list[dict[str, Any]] = []
    for d in days:
        s = starts_d.get(d)
        e = energy_d.get(d)
        temps = t_out_d.get(d, [])
        t_mean = fmean(temps) if len(temps) >= 18 else None
        hdd = max(0.0, hdd_base - t_mean) if t_mean is not None else None
        e_sum = sum(max(0.0, x) for x in e) if e else None
        mins: list[float] = []
        below = 0
        seen_comfort = False
        for z in zones:
            for t, low in z.comfort.items():
                if low is None or t.astimezone(tz).date() != d:
                    continue
                seen_comfort = True
                if t in z.hourly_min:
                    mins.append(z.hourly_min[t])
                if t in z.hourly_mean and z.hourly_mean[t] < low:
                    below += 1
        rows.append(
            {
                "date": d.isoformat(),
                "complete": d < today,
                "starts": sum(max(0.0, x) for x in s) if s else None,
                "energy_kwh": _r(e_sum),
                "t_out_mean": _r(t_mean, 1),
                "hdd": _r(hdd, 1),
                "kwh_per_hdd": _r(e_sum / hdd) if e_sum is not None and hdd is not None and hdd >= HDD_MIN else None,
                "min_leading": _r(min(mins)) if mins else None,
                "below_comfort_h": below if seen_comfort and mins else None,
            }
        )
    return rows


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r[key] is not None]
    return fmean(vals) if vals else None


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Averages over complete days; kWh per HDD as ratio of sums (robust against mild days)."""
    rows = [r for r in rows if r["complete"]]
    if not rows:
        return None
    pairs = [(r["energy_kwh"], r["hdd"]) for r in rows if r["energy_kwh"] is not None and r["hdd"] is not None]
    hdd_sum = sum(h for _, h in pairs)
    mins = [r["min_leading"] for r in rows if r["min_leading"] is not None]
    return {
        "n_days": len(rows),
        "starts_per_day": _r(_mean(rows, "starts"), 1),
        "energy_per_day": _r(_mean(rows, "energy_kwh"), 1),
        "kwh_per_hdd": _r(sum(e for e, _ in pairs) / hdd_sum) if hdd_sum >= HDD_MIN else None,
        "hdd_sum": _r(sum(r["hdd"] for r in rows if r["hdd"] is not None), 1),
        "t_out_mean": _r(_mean(rows, "t_out_mean"), 1),
        "min_leading": _r(min(mins)) if mins else None,
        "below_comfort_h_per_day": _r(_mean(rows, "below_comfort_h"), 1),
    }


def summary(rows: list[dict[str, Any]], control_since: date | None) -> dict[str, Any]:
    """All complete days, and – once control was enabled – before vs. since that day."""
    before = after = None
    if control_since is not None:
        before = aggregate([r for r in rows if date.fromisoformat(r["date"]) < control_since])
        after = aggregate([r for r in rows if date.fromisoformat(r["date"]) >= control_since])
    return {"all": aggregate(rows), "before": before, "after": after}
