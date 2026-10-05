"""Warm start from the recorder: learn zone models from the last weeks instead of starting from the prior."""
from __future__ import annotations

import logging
from datetime import timedelta
from statistics import fmean
from typing import TYPE_CHECKING

from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FLOW_TEMP_SENSOR,
    CONF_FORGETTING,
    CONF_GAIN_ENTITIES,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_TEMP_SENSORS,
    CONF_VALVE_ENTITY,
    CONF_WINDOW_ENTITIES,
    DEFAULT_FORGETTING,
)
from .core.forecast import fetch_forecast
from .core.model import OnlineZoneModel
from .core.rules import binary_value
from .core.series import hourly_fraction, hourly_mean
from .core.view import align
from .core.warmstart import WarmstartInputs, build_records, warm_train
from .history import async_fetch_states, async_fetch_statistics

if TYPE_CHECKING:
    from .coordinator import ThermocastCoordinator

_LOGGER = logging.getLogger(__name__)
WARMSTART_DAYS = 30  # Open-Meteo serves up to 92 past days; the RLS forgets after ~10 days anyway
MIN_LEARNED_HOURS = 24
HOUR = timedelta(hours=1)


async def async_warmstart(coordinator: ThermocastCoordinator, only_fresh: bool) -> dict[str, int]:
    """Re-learn zone models from history. ``only_fresh``: only zones that have not learned anything yet.

    Returns learned hours per zone id (0 = not enough data, model left unchanged).
    """
    hass = coordinator.hass
    if "recorder" not in hass.config.components:
        return {}
    targets = {sid: z for sid, z in coordinator.zones.items() if not only_fresh or z.model.n_updates == 0}
    if not targets:
        return {}
    cfg = coordinator.config_entry.data
    tz = dt_util.get_time_zone(hass.config.time_zone)
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=WARMSTART_DAYS)
    hours = [start + i * HOUR for i in range(WARMSTART_DAYS * 24)]

    numeric_ids: set[str] = {e for e in (cfg.get(CONF_OUTDOOR_SENSOR), cfg.get(CONF_FLOW_TEMP_SENSOR)) if e}
    binary_ids: set[str] = {e for e in (cfg.get(CONF_HEATING_ACTIVE),) if e}
    for z in targets.values():
        numeric_ids |= set(z.cfg.get(CONF_TEMP_SENSORS, [])) | set(z.cfg.get(CONF_NEIGHBOR_SENSORS, []))
        numeric_ids |= set(z.cfg.get(CONF_GAIN_ENTITIES, []))
        if z.cfg.get(CONF_VALVE_ENTITY):
            numeric_ids.add(z.cfg[CONF_VALVE_ENTITY])
        binary_ids |= set(z.cfg.get(CONF_WINDOW_ENTITIES, []))

    stats = await async_fetch_statistics(hass, numeric_ids, start, end, {"mean"}) or {}
    without_stats = {e for e in numeric_ids if not stats.get(e)}  # no state_class -> raw states
    states = await async_fetch_states(hass, without_stats | binary_ids, start, end) or {}

    def numeric(eid: str | None) -> list[float | None]:
        if not eid:
            return [None] * len(hours)
        if stats.get(eid):
            return [stats[eid].get(h, {}).get("mean") for h in hours]
        return hourly_mean(states.get(eid, []), hours, end)

    def binary(eid: str) -> list[float | None]:
        return hourly_fraction(states.get(eid, []), hours, end, binary_value)

    orientations = {s.key: (s.key, s.tilt, s.azimuth) for z in targets.values() for s in z.model.spec.surfaces}
    try:
        fc = await fetch_forecast(
            async_get_clientsession(hass), hass.config.latitude, hass.config.longitude, orientations.values(),
            past_days=WARMSTART_DAYS + 1, forecast_days=1,
        )
    except Exception as err:  # noqa: BLE001 - no irradiance history -> no warm start (zones with sun need it)
        _LOGGER.warning("Thermocast warm start: Open-Meteo history unavailable: %s", err)
        fc = None
    t_out = numeric(cfg.get(CONF_OUTDOOR_SENSOR))
    if fc is not None:
        t_fc = align(fc.times, fc.t_out, hours)
        t_out = [m if m is not None else f for m, f in zip(t_out, t_fc)]
    flow = numeric(cfg.get(CONF_FLOW_TEMP_SENSOR))
    heating = binary(cfg[CONF_HEATING_ACTIVE]) if cfg.get(CONF_HEATING_ACTIVE) else None
    forgetting = float(coordinator.config_entry.options.get(CONF_FORGETTING, DEFAULT_FORGETTING))

    result: dict[str, int] = {}
    for sid, z in targets.items():
        sensors = [numeric(e) for e in z.cfg.get(CONF_TEMP_SENSORS, [])]
        temp = [fmean(v) if (v := [s[i] for s in sensors if s[i] is not None]) else None for i in range(len(hours))]
        keys = [s.key for s in z.model.spec.surfaces]
        inp = WarmstartInputs(
            hours=hours,
            temp=temp,
            t_out=t_out,
            irr={k: align(fc.times, fc.irr[k], hours) for k in keys if fc is not None and k in fc.irr},
            flow=flow,
            heating=heating,
            valve=numeric(z.cfg.get(CONF_VALVE_ENTITY))
            if z.cfg.get(CONF_HEAT_TYPE) == "radiator" and z.cfg.get(CONF_VALVE_ENTITY)
            else None,
            neighbors=[numeric(e) for e in z.cfg.get(CONF_NEIGHBOR_SENSORS, [])],
            gains=[numeric(e) for e in z.cfg.get(CONF_GAIN_ENTITIES, [])],
            window=[binary(e) for e in z.cfg.get(CONF_WINDOW_ENTITIES, [])],
        )
        records = build_records(inp, keys)
        fresh = OnlineZoneModel(z.model.spec, forgetting=forgetting)
        res = await hass.async_add_executor_job(warm_train, fresh, records, tz)
        valid = sum(1 for _, rec, _ in records if rec.valid)
        if valid < MIN_LEARNED_HOURS:
            _LOGGER.info("Thermocast warm start: zone %s has only %s usable hours – skipped", z.title, valid)
            result[sid] = 0
            continue
        z.model = res.model
        z.log.load(res.log)
        z.params = res.params
        if res.q_on is not None:
            z.q_on = res.q_on
        coordinator.events.add(dt_util.utcnow(), "warmstart", zone=sid, detail=f"{valid} h")
        result[sid] = valid
        _LOGGER.info("Thermocast warm start: zone %s learned %s hours", z.title, valid)
    return result
