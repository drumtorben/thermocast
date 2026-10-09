"""Model tab (quality per zone) and the JSON export."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import CONF_GAIN_ENTITIES, CONF_HEAT_TYPE, CONF_LEADS_RELEASE, CONF_NEIGHBOR_SENSORS
from .core.model import OnlineZoneModel
from .core.quality import ForecastLog, QualityInput, zone_quality
from .core.weather import weather_summary

if TYPE_CHECKING:
    from .coordinator import ThermocastCoordinator, ZoneRuntime

MODEL_VERSION = 1
EXPORT_VERSION = 1
QUALITY_DAYS = 7


def _quality_input(coordinator: ThermocastCoordinator, sid: str, z: ZoneRuntime) -> QualityInput:
    """Copies taken on the event loop – the executor never touches live coordinator state."""
    model = OnlineZoneModel(z.model.spec, forgetting=z.model.forgetting)
    model.load_dict(z.model.to_dict())
    flog = ForecastLog()
    flog.load(z.flog.to_dict())
    labels: dict[str, str] = {}
    for i, e in enumerate(z.cfg.get(CONF_NEIGHBOR_SENSORS, [])):
        labels[f"neighbor:{i}"] = coordinator.view_builder._friendly(e)
    for i, e in enumerate(z.cfg.get(CONF_GAIN_ENTITIES, [])):
        labels[f"gain:{i}"] = coordinator.view_builder._friendly(e)
    return QualityInput(
        id=sid, name=z.title, leads=bool(z.cfg.get(CONF_LEADS_RELEASE, True)),
        heat_type=z.cfg.get(CONF_HEAT_TYPE, "fbh"), model=model, q_on=z.q_on, entries=z.log.to_list(),
        flog=flog, params=list(z.params), labels=labels,
    )


async def async_model_view(coordinator: ThermocastCoordinator) -> dict[str, Any]:
    now = dt_util.utcnow()
    tz = dt_util.get_time_zone(coordinator.hass.config.time_zone)
    zones = []
    for inp in [_quality_input(coordinator, sid, z) for sid, z in coordinator.zones.items()]:
        zones.append(await coordinator.hass.async_add_executor_job(zone_quality, inp, tz, now, QUALITY_DAYS))
    zones.sort(key=lambda zv: (not zv["leads"], zv["name"].lower()))
    weather = weather_summary(coordinator.outdoor_bias, coordinator.forecast, now, tz)
    return {
        "version": MODEL_VERSION, "generated_at": now.isoformat(), "days": QUALITY_DAYS, "zones": zones,
        "weather": weather,
    }


async def async_export(coordinator: ThermocastCoordinator) -> dict[str, Any]:
    """Everything needed to analyse the models offline (e.g. polars in a notebook)."""
    entry = coordinator.config_entry
    return {
        "version": EXPORT_VERSION,
        "exported_at": dt_util.utcnow().isoformat(),
        "config": dict(entry.data),
        "options": dict(entry.options),
        "zones": {
            sid: {
                "title": z.title,
                "config": z.cfg,
                "q_on": z.q_on,
                "model": z.model.to_dict(),
                "log": z.log.to_list(),
                "forecast_log": z.flog.to_dict(),
                "params": z.params,
            }
            for sid, z in coordinator.zones.items()
        },
        "outdoor_bias": coordinator.outdoor_bias.to_dict(),
        "events": coordinator.events.to_list(),
        "control_since": coordinator.control_since.isoformat() if coordinator.control_since else None,
        "view": coordinator.view,
        "model_view": await async_model_view(coordinator),
    }
