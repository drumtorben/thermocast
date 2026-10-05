"""KPI tab: daily burner starts, heating energy, degree days and comfort from recorder statistics."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from statistics import fmean
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_BURNER_STARTS,
    CONF_HEAT_ENERGY,
    CONF_LEADS_RELEASE,
    CONF_OUTDOOR_SENSOR,
    CONF_TEMP_SENSORS,
    HDD_BASE,
)
from .core.kpi import ZoneKpiInput, daily_kpis, summary
from .history import async_fetch_statistics

if TYPE_CHECKING:
    from .coordinator import ThermocastCoordinator

KPI_VERSION = 1
MAX_DAYS = 365


async def async_kpis(coordinator: ThermocastCoordinator, days: int) -> dict[str, Any]:
    from .coordinator import zone_comfort

    hass = coordinator.hass
    cfg, options = coordinator.config_entry.data, coordinator.config_entry.options
    days = max(1, min(int(days), MAX_DAYS))
    tz = dt_util.get_time_zone(hass.config.time_zone)
    now = dt_util.utcnow()
    today = now.astimezone(tz).date()
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, time(0), tzinfo=tz).astimezone(dt_util.UTC)
    day_list = [first + timedelta(days=i) for i in range(days)]

    starts_eid, energy_eid, outdoor = options.get(CONF_BURNER_STARTS), options.get(CONF_HEAT_ENERGY), cfg.get(
        CONF_OUTDOOR_SENSOR
    )
    leading = [z for z in coordinator.zones.values() if z.cfg.get(CONF_LEADS_RELEASE, True)]
    temp_ids = {e for z in leading for e in z.cfg.get(CONF_TEMP_SENSORS, [])}
    ids = {e for e in (starts_eid, energy_eid, outdoor) if e} | temp_ids
    stats = await async_fetch_statistics(hass, ids, start, now, {"mean", "min", "change"})

    missing: list[str] = []
    if stats is None:
        missing.append("recorder")
        stats = {}

    def series(eid: str | None, key: str, name: str) -> dict[datetime, float] | None:
        if not eid:
            missing.append(f"{name}:not_configured")
            return None
        rows = stats.get(eid)
        if not rows:
            if "recorder" not in missing:
                missing.append(f"{name}:no_statistics:{eid}")
            return None
        return {t: v[key] for t, v in rows.items() if key in v}

    starts = series(starts_eid, "change", "burner_starts")
    energy = series(energy_eid, "change", "heat_energy")
    t_out = series(outdoor, "mean", "outdoor")

    zones: list[ZoneKpiInput] = []
    for z in leading:
        mins: dict[datetime, list[float]] = {}
        means: dict[datetime, list[float]] = {}
        for eid in z.cfg.get(CONF_TEMP_SENSORS, []):
            rows = stats.get(eid)
            if not rows and "recorder" not in missing:
                missing.append(f"zone:no_statistics:{eid}")
            for t, v in (rows or {}).items():
                if "min" in v:
                    mins.setdefault(t, []).append(v["min"])
                if "mean" in v:
                    means.setdefault(t, []).append(v["mean"])
        hours = sorted(set(mins) | set(means))
        zones.append(
            ZoneKpiInput(
                hourly_min={t: min(v) for t, v in mins.items()},
                hourly_mean={t: fmean(v) for t, v in means.items()},
                comfort={t: zone_comfort(z, t) for t in hours},
            )
        )

    rows = daily_kpis(
        days=day_list, tz=tz, today=today, starts=starts, energy=energy, t_out=t_out, zones=zones, hdd_base=HDD_BASE
    )
    since = coordinator.control_since
    return {
        "version": KPI_VERSION,
        "generated_at": now.isoformat(),
        "tz": hass.config.time_zone,
        "hdd_base": HDD_BASE,
        "days": rows,
        "control_since": since.isoformat() if since else None,
        "summary": summary(rows, since.astimezone(tz).date() if since else None),
        "missing": missing,
    }
