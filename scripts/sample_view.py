"""Write sample panel responses from synthetic data (no Home Assistant needed).

    uv run python scripts/sample_view.py

Creates in custom_components/thermocast/frontend/dev/:
  sample-view.json   – thermocast/subscribe (tab "Jetzt & Plan")
  sample-model.json  – thermocast/model     (tab "Modell")
  sample-kpis.json   – thermocast/kpis      (tab "KPIs")
"""
from __future__ import annotations

import json
import math
import random
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "custom_components" / "thermocast"))
sys.path.insert(0, str(ROOT))

from core.forecast import Forecast
from core.kpi import ZoneKpiInput, daily_kpis, summary
from core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec
from core.planner import ZonePlanInput, charge_cost
from core.quality import ForecastLog, QualityInput, RingLog, hindcast, param_snapshot, zone_quality
from core.rollout import ActuatorState, block_lengths_for
from core.rules import Rules
from core.view import ZoneViewInput, build_view, compute_outlook, make_window
from core.weather import OutdoorBias, weather_summary

from tests.synthetic import simulate

TZ_NAME = "Europe/Berlin"
TZ = ZoneInfo(TZ_NAME)
NOW = datetime(2026, 10, 4, 18, 5, tzinfo=UTC)
HOUR = timedelta(hours=1)
DEV = ROOT / "custom_components" / "thermocast" / "frontend" / "dev"
SURFACES = (
    SurfaceSpec(kind="window", azimuth=90, tilt=90, name="Fenster Ost"),
    SurfaceSpec(kind="roof", azimuth=180, tilt=40, name="Schräge Süd"),
)
COLD = -6.0  # shift the synthetic autumn into a colder week, so the planner has work to do
ZONES = [  # id, name, heat type, leads, comfort low, upper bound, base, BT control (quiet 22–06)
    ("zone_eg", "EG", "fbh", True, 20.2, 21.5, 18.5, False),
    ("zone_eltern", "Elternschlafzimmer", "radiator", False, 19.0, 20.0, 17.0, True),
]


def _irr(sim, k: int) -> dict[str, float]:
    return {key: float(v[k]) for key, v in sim["irr"].items()}


def _rec(sim, k: int, q: bool = True) -> HourRecord:
    return HourRecord(
        temp=float(sim["air"][k]), t_out=float(sim["t_out"][k]) + COLD, irr=_irr(sim, k),
        q=float(sim["q"][k]) if q else 0.0,
    )


def _train(sim, heat_type: str, until: int, hour_of):
    """Train like the coordinator does and keep its logs (14-day hour log, forecast log, daily params)."""
    rng = random.Random(7)
    m = OnlineZoneModel(ZoneSpec(heat_type=heat_type, surfaces=SURFACES))
    log, flog, params = RingLog(), ForecastLog(), []
    for k in range(until):
        t = hour_of(k)
        if k >= until - 14 * 24 and k + 24 < len(sim["t_out"]):
            # operational forecast issued at this hour: forecast weather (noisy) + actual heating
            fut = [
                HourRecord(temp=0.0, t_out=float(sim["t_out"][j]) + COLD + rng.gauss(0, 0.8), irr=_irr(sim, j),
                           q=float(sim["q"][j]))
                for j in range(k, k + 24)
            ]
            flog.record(t, m.predict(float(sim["air"][k]), fut))
        rec = _rec(sim, k)
        err = m.update(rec, float(sim["air"][k + 1]))
        if k >= until - 14 * 24:
            log.append({"t": t.isoformat(), "rec": rec.to_dict(), "temp_next": float(sim["air"][k + 1]), "err": err})
        day = t.astimezone(TZ).date().isoformat()
        if k >= until - 30 * 24 and (not params or params[-1]["date"] != day):
            params.append({"date": day, **param_snapshot(m)})
    return m, log, flog, params


def main() -> None:
    sim = simulate(days=40, seed=4)
    total = len(sim["t_out"])
    w = make_window(NOW, TZ, TZ_NAME)
    n, now = len(w.hours), w.now_index
    base = 30 * 24 - now  # sim index of window hour 0

    def hour_of(k: int) -> datetime:
        return w.hours[0] + (k - base) * HOUR

    def comfort(t: datetime, low: float) -> float | None:
        return low if 6 <= t.astimezone(TZ).hour < 22 else None

    def quiet(t: datetime) -> bool:
        hour = t.astimezone(TZ).hour
        return hour >= 22 or hour < 6

    plan_inputs, view_zones, quality = [], [], []
    for zid, name, heat_type, leads, low, high, base_t, bt in ZONES:

        def floor(t: datetime, low=low, base_t=base_t) -> float:
            return comfort(t, low) or base_t

        def cap(t: datetime, high=high, base_t=base_t, bt=bt) -> float | None:
            return (base_t if quiet(t) else high) if bt else None

        m, log, flog, params = _train(sim, heat_type, base + now, hour_of)
        entries = log.to_list()
        hc = {e["t"]: h for e, h in zip(entries, hindcast(m, entries, TZ))}
        fut_k = [k for k in range(base + now, base + now + 76) if k < total]
        times = [w.hours[now] + timedelta(hours=h + 1) for h in range(len(fut_k))]
        plan_inputs.append(
            ZonePlanInput(
                name=zid, model=m, temp_now=round(float(sim["air"][base + now]), 2),
                future=[HourRecord(temp=0.0, t_out=float(sim["t_out"][k]) + COLD, irr=_irr(sim, k)) for k in fut_k],
                comfort_low=[floor(t) for t in times], q_on=15.0, leads_release=leads,
                comfort_high=[high] * len(times), charge_cap=[cap(t - HOUR) for t in times],
            )
        )
        view_zones.append(
            ZoneViewInput(
                id=zid, name=name, heat_type=heat_type, leads=leads,
                measured=[round(float(sim["air"][base + i]), 2) if i <= now else None for i in range(n)],
                comfort_low=[comfort(h, low) for h in w.hours], group_labels=m.group_labels(),
                contrib_past=[(h["contrib"] if (h := hc.get(w.hours[i].isoformat())) else None) for i in range(now)],
                forecast6=[
                    (p[0] if (p := flog.predicted(w.hours[i], 6)) else None) if i <= now else None for i in range(n)
                ],
                comfort_high=[high] * n, floor=[floor(h) for h in w.hours],
                quiet=[bt and quiet(h) for h in w.hours], bt_control=bt,
                bt_on=[cap(h) for h in w.hours] if bt else [],
                bt_off=[base_t if quiet(h) else floor(h) for h in w.hours] if bt else [],
                bt_past=[(high if sim["q"][base + i] > 0 else floor(w.hours[i])) if i < now else None for i in range(n)]
                if bt else [],
            )
        )
        quality.append(
            zone_quality(
                QualityInput(id=zid, name=name, leads=leads, heat_type=heat_type, model=m, q_on=15.0,
                             entries=entries, flog=flog, params=params),
                TZ, NOW,
            )
        )

    steps = n - now
    day_index = [w.hours[min(now + h, n - 1)].astimezone(TZ).toordinal() for h in range(steps)]
    outlook = compute_outlook(
        plan_inputs, steps, Rules(), ActuatorState(on=False), day_index, w.hours[now], block_lengths=block_lengths_for(3),
        cost_fn=charge_cost(0.8),
    )
    on_now = bool(outlook.rollout.on and outlook.rollout.on[0])
    heating = [(1.0 if sim["q"][base + i] > 0 else 0.0) if i <= now else None for i in range(n)]
    dhw = [(0.3 if w.hours[i].astimezone(TZ).hour in (6, 18) else 0.0) if i <= now else None for i in range(n)]
    view = build_view(
        window=w, tz=TZ, generated_at=NOW,
        t_out_measured=[round(float(sim["t_out"][base + i]) + COLD, 1) if i <= now else None for i in range(n)],
        t_out_forecast=[round(float(sim["t_out"][base + i]) + COLD, 1) for i in range(n)],
        irr=[
            {"key": key, "label": label, "values": [round(float(sim["irr"][key][base + i])) for i in range(n)]}
            for key, label in (("90_90", "Fenster Ost"), ("40_180", "Schräge Süd"))
        ],
        heating_actual=heating,
        release=[True if i <= now else None for i in range(n)],
        planner=[(h >= 0.5) if h is not None else None for h in heating],
        zones=view_zones, outlook=outlook, z=1.0,
        decision={
            "planner_wants": outlook.rollout.first.heat_now, "applied": True, "control_enabled": False,
            "override": "observe", "failsafe_reason": None, "switches_today": 0, "max_switches": 12,
            "rules": {"min_block_h": 3, "min_pause_h": 2, "max_switches": 12, "starts_weight": 80.0,
                  "anti_cycle_min": 45.0}, "since_last_change_min": None,
            "forecast_age_min": 12, "release_entity": "select.thermostat_hc1_summersetmode", "release_state": True,
            "bt": {"zone_eltern": {"target": 20.0 if on_now else 19.0, "reason": "observe", "override_until": None}},
        },
        plan_change={
            "previous_block": {"start": w.hours[now + 6].isoformat(), "end": w.hours[now + 10].isoformat()},
            "current_block": {"start": w.hours[now + 5].isoformat(), "end": w.hours[now + 9].isoformat()},
            "cause": {"kind": "t_out", "delta": -1.2, "at": w.hours[now + 10].isoformat(), "zone": None},
        },
        events=[
            {"time": (w.hours[8] + timedelta(minutes=20)).isoformat(), "type": "window_open", "zone": "zone_eltern"},
            {"time": (w.hours[27] + timedelta(minutes=5)).isoformat(), "type": "forecast_failed"},
        ],
        errors=[],
        dhw=dhw,
        burner_starts=[(2.0 if heating[i] and not (i and heating[i - 1]) else 0.5 if heating[i] else 0.0)
                       if i < now else None for i in range(n)],
    )
    def _sample_weather() -> dict:
        """A sensor ~1.3 K warmer than the forecast (more at noon), the models 0–2 K apart."""
        bias = OutdoorBias()
        hours = [NOW - (14 * 24 - i) * HOUR for i in range(14 * 24)]
        bias.learn(hours, [5.0 + 1.3 + 0.6 * math.cos(2 * math.pi * (t.hour - 11) / 24) for t in hours], [5.0] * len(hours))
        times = [NOW.replace(minute=0) + i * HOUR for i in range(-24, 48)]
        fc = Forecast(times=times, t_out=[5.0] * len(times), fetched_at=NOW,
                      t_out_spread=[abs(math.sin(i / 9)) * 2.0 for i in range(len(times))])
        return weather_summary(bias, fc, NOW, TZ)

    model_view = {
        "version": 1, "generated_at": NOW.isoformat(), "days": 7, "zones": quality, "weather": _sample_weather(),
    }
    kpis = _sample_kpis()
    for name, data in (("sample-view.json", view), ("sample-model.json", model_view), ("sample-kpis.json", kpis)):
        path = DEV / name
        path.write_text(json.dumps(data, ensure_ascii=False))
        print(f"wrote {path.relative_to(ROOT)} ({path.stat().st_size // 1024} KB)")


def _sample_kpis(days: int = 30) -> dict:
    """60 days of plausible hourly statistics; control enabled 12 days ago (fewer starts afterwards)."""
    rng = random.Random(3)
    today = NOW.astimezone(TZ).date()
    since = today - timedelta(days=12)
    start = datetime(today.year, today.month, today.day, tzinfo=TZ).astimezone(UTC) - timedelta(days=days - 1)
    starts, energy, t_out = {}, {}, {}
    zone = ZoneKpiInput()
    t = start
    while t <= NOW:
        local = t.astimezone(TZ)
        d = local.date()
        temp = 8 + 4 * math.sin(2 * math.pi * (local.hour - 9) / 24) - 0.12 * (d - start.date()).days + rng.gauss(0, 1)
        heat = max(0.0, 15.0 - temp)
        controlled = d >= since
        starts[t] = rng.choice([0, 0, 1]) if controlled else rng.choice([1, 1, 2, 2, 3])
        energy[t] = round(heat * (0.09 if controlled else 0.1) * rng.uniform(0.8, 1.2), 3)
        t_out[t] = temp
        zone.hourly_mean[t] = 20.6 + rng.gauss(0, 0.2)
        zone.hourly_min[t] = zone.hourly_mean[t] - abs(rng.gauss(0.2, 0.1))
        zone.comfort[t] = 20.2 if 6 <= local.hour < 22 else None
        t += HOUR
    day_list = [today - timedelta(days=days - 1 - i) for i in range(days)]
    rows = daily_kpis(days=day_list, tz=TZ, today=today, starts=starts, energy=energy, t_out=t_out, zones=[zone])
    since_dt = datetime(since.year, since.month, since.day, 9, tzinfo=TZ)
    return {
        "version": 1, "generated_at": NOW.isoformat(), "tz": TZ_NAME, "hdd_base": 15.0, "days": rows,
        "control_since": since_dt.isoformat(), "summary": summary(rows, since), "missing": [],
    }


if __name__ == "__main__":
    main()
