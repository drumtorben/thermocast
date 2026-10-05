"""Assemble the panel view next to the control path (never inside it)."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CONFIDENCE_Z,
    CONF_DHW_ENTITY,
    CONF_GAIN_ENTITIES,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_LEADS_RELEASE,
    CONF_MIN_BLOCK_H,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    CONF_TEMP_SENSORS,
    DEFAULT_CONFIDENCE_Z,
    DEFAULT_MIN_BLOCK_H,
    DOMAIN,
)
from .core.explain import PlanSnapshot, plan_change, snapshot_from
from .core.model import Prediction
from .core.quality import hindcast
from .core.rollout import ActuatorState, block_lengths_for
from .core.rules import apply_rules, binary_value, release_state
from .core.series import hourly_fraction, hourly_mean
from .core.view import ZoneViewInput, align, build_view, compute_outlook, make_window
from .history import async_fetch_states

if TYPE_CHECKING:
    from .coordinator import ThermocastCoordinator, ThermocastData

_LOGGER = logging.getLogger(__name__)
HOUR = timedelta(hours=1)


def _round(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(x, nd)


class ViewBuilder:
    def __init__(self, coordinator: ThermocastCoordinator) -> None:
        self._c = coordinator
        self.view: dict[str, Any] | None = None
        self._key: tuple | None = None
        self._window_start: datetime | None = None
        self._window_end: datetime | None = None
        self._snapshots: dict[datetime, PlanSnapshot] = {}
        # shadow actuator: in observe mode the real actuator never switches, so the rollout would
        # start without block/pause history. The shadow applies the same rules virtually.
        self._shadow_on = False
        self._shadow_change: datetime | None = None
        self._shadow_switches = 0
        self._shadow_day = None

    def mark_unloaded(self) -> None:
        self.view = {"error": "unloaded"}

    def invalidate(self) -> None:
        """Force a full rebuild on the next refresh (e.g. after a warm start replaced the models)."""
        self._key = None

    def _update_shadow(self, now: datetime, data: ThermocastData) -> None:
        today = dt_util.as_local(now).date()
        if self._shadow_day != today:
            self._shadow_day, self._shadow_switches = today, 0
        elapsed = (now - self._shadow_change).total_seconds() / 3600 if self._shadow_change else math.inf
        target, _ = apply_rules(data.want_heat, self._shadow_on, elapsed, self._shadow_switches, self._c.actuator.rules)
        if target != self._shadow_on:
            self._shadow_on, self._shadow_change = target, now
            self._shadow_switches += 1

    def _actuator_state(self, now: datetime, data: ThermocastData) -> ActuatorState:
        """Starting point of the rollout: the real actuator when controlling, else the shadow."""
        a = self._c.actuator
        if data.control_enabled:
            return ActuatorState(on=data.release_on, since_h=a.elapsed_h(now), switches_today=a.switches_today)
        since = (now - self._shadow_change).total_seconds() / 3600 if self._shadow_change else math.inf
        return ActuatorState(on=self._shadow_on, since_h=since, switches_today=self._shadow_switches)

    async def async_refresh(self, now: datetime, data: ThermocastData) -> None:
        """Rebuild on a new hour / forecast / fail-safe change, otherwise only refresh decision + events."""
        c = self._c
        try:
            self._update_shadow(now, data)
            hour0 = now.replace(minute=0, second=0, microsecond=0)
            key = (hour0, c.forecast.fetched_at if c.forecast else None, data.failsafe_reason)
            if self.view is None or "error" in self.view or key != self._key:
                self.view = await self._async_build(now, hour0, data)
                self._key = key
            else:
                self.view = {
                    **self.view,
                    "generated_at": now.isoformat(),
                    "decision": self._decision(now, data),
                    "events": self._events(),
                }
        except Exception as err:
            _LOGGER.exception("Thermocast: building the panel view failed")
            self.view = {"error": f"{type(err).__name__}: {err}"}

    # ------------------------------------------------------------------ parts
    def _decision(self, now: datetime, data: ThermocastData) -> dict[str, Any]:
        a = self._c.actuator
        rules = a.rules
        return {
            "planner_wants": data.planner_heat,
            "applied": data.release_on,
            "control_enabled": data.control_enabled,
            "override": data.override,
            "failsafe_reason": data.failsafe_reason,
            "switches_today": a.switches_today,
            "max_switches": rules.max_switches,
            "rules": {
                "min_block_h": rules.min_block_h,
                "min_pause_h": rules.min_pause_h,
                "max_switches": rules.max_switches,
            },
            "since_last_change_min": round(a.elapsed_h(now) * 60) if a.last_change else None,
            "forecast_age_min": _round(data.forecast_age_min, 0),
            "release_entity": a.entity_id,
            "release_state": a.entity_is_on(),
        }

    def _events(self) -> list[dict[str, Any]]:
        start, end = self._window_start, self._window_end
        return [
            e for e in self._c.events.to_list()
            if start is None or end is None or start <= datetime.fromisoformat(e["time"]) < end
        ]

    def _friendly(self, entity_id: str) -> str:
        st = self._c.hass.states.get(entity_id)
        return st.name if st else entity_id

    async def _async_build(self, now: datetime, hour0: datetime, data: ThermocastData) -> dict[str, Any]:
        from .coordinator import build_plan_inputs, zone_comfort

        c = self._c
        hass = c.hass
        cfg = c.config_entry.data
        tz_name = hass.config.time_zone
        tz = dt_util.get_time_zone(tz_name)
        window = make_window(now, tz, tz_name)
        self._window_start, self._window_end = window.hours[0], window.end
        hours, n = window.hours, len(window.hours)
        errors: list[str] = []

        # ---------------------------------------------------------------- past
        planner_eid = er.async_get(hass).async_get_entity_id(
            "binary_sensor", DOMAIN, f"{c.config_entry.entry_id}_heating_release"
        )
        sensors = {sid: list(z.cfg.get(CONF_TEMP_SENSORS, [])) for sid, z in c.zones.items()}
        outdoor, pump, rel = cfg.get(CONF_OUTDOOR_SENSOR), cfg.get(CONF_HEATING_ACTIVE), cfg.get(CONF_RELEASE_ENTITY)
        dhw_eid = c.config_entry.options.get(CONF_DHW_ENTITY)
        wanted = {e for lst in sensors.values() for e in lst} | {outdoor, pump, rel, planner_eid, dhw_eid}

        states = await async_fetch_states(hass, {e for e in wanted if e}, hours[0], now)
        if states is None:
            errors.append("no_recorder")
            states = {}

        def mean_of(eid: str | None) -> list[float | None]:
            return hourly_mean(states.get(eid, []), hours, now) if eid else [None] * n

        def fraction_of(eid: str | None, value_of) -> list[float | None]:
            return hourly_fraction(states.get(eid, []), hours, now, value_of) if eid else [None] * n

        rel_domain = rel.split(".")[0] if rel else ""

        def rel_value(s: str) -> float | None:
            v = release_state(s, rel_domain, cfg.get(CONF_RELEASE_ON), cfg.get(CONF_RELEASE_OFF))
            return None if v is None else float(v)

        release = [None if f is None else f >= 0.5 for f in fraction_of(rel, rel_value)]
        planner = [None if f is None else f >= 0.5 for f in fraction_of(planner_eid, binary_value)]
        heating_actual = fraction_of(pump, binary_value)
        t_meas = mean_of(outdoor)

        # ------------------------------------------------------------ forecast
        fc = c.forecast
        idx0 = fc.index_of(now) if fc else None
        zval = float(c.config_entry.options.get(CONF_CONFIDENCE_Z, DEFAULT_CONFIDENCE_Z))
        t_fc: list[float | None] = [None] * n
        irr: list[dict[str, Any]] = []
        outlook = None
        change = None
        labels: dict[str, str] = {}
        for z in c.zones.values():
            for s in z.model.spec.surfaces:
                labels.setdefault(s.key, s.name or f"{s.kind} {round(s.azimuth)}°")
        if fc is None or idx0 is None:
            errors.append("no_forecast")
        else:
            t_fc = align(fc.times, fc.t_out, hours)
            irr = [
                {
                    "key": key,
                    "label": labels.get(key, key),
                    "values": [_round(v, 0) for v in align(fc.times, vals, hours)],
                }
                for key, vals in fc.irr.items()
                if key != "0_0" or not labels
            ]
            inputs = build_plan_inputs(hass, c.zones, fc, idx0, len(fc.times) - idx0 - 1)
            if inputs:
                state = self._actuator_state(now, data)
                steps = n - window.now_index
                day_index = [hours[min(window.now_index + h, n - 1)].astimezone(tz).toordinal() for h in range(steps)]
                lengths = block_lengths_for(float(c.config_entry.options.get(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H)))
                outlook = await hass.async_add_executor_job(
                    partial(
                        compute_outlook, inputs, steps, c.actuator.rules, state, day_index, hour0,
                        z=zval, block_lengths=lengths,
                    )
                )
                snap = snapshot_from(outlook.rollout.first, outlook.first_inputs, hour0)
                self._snapshots = {h: s for h, s in self._snapshots.items() if h >= hour0 - HOUR}
                self._snapshots[hour0] = snap
                change = plan_change(self._snapshots.get(hour0 - HOUR), snap, {zi.name: zi.temp_now for zi in inputs})
                # operational forecast log (model tab: forecast quality per horizon)
                for zid, tr in outlook.rollout.zones.items():
                    if zid in c.zones:
                        c.zones[zid].flog.record(hour0, Prediction(mean=tr.mean, std=tr.std))

        # --------------------------------------------------------------- zones
        zones: list[ZoneViewInput] = []
        for sid, z in c.zones.items():
            series = [mean_of(e) for e in sensors[sid]]
            measured = [
                fmean(vals) if (vals := [s[i] for s in series if s[i] is not None]) else None for i in range(n)
            ]
            group_labels = dict(z.model.group_labels())
            for i, e in enumerate(z.cfg.get(CONF_NEIGHBOR_SENSORS, [])):
                group_labels[f"neighbor:{i}"] = self._friendly(e)
            for i, e in enumerate(z.cfg.get(CONF_GAIN_ENTITIES, [])):
                group_labels[f"gain:{i}"] = self._friendly(e)
            # the past: causes from the hindcast (measured inputs), forecast made 6 h earlier
            entries = z.log.to_list()
            hc = await hass.async_add_executor_job(hindcast, z.model, entries, tz) if entries else []
            hc_by_hour = {e["t"]: h for e, h in zip(entries, hc)}
            past = range(window.now_index)
            contrib_past = [
                (h["contrib"] if (h := hc_by_hour.get(hours[i].isoformat())) else None) for i in past
            ]
            forecast6 = [
                (p[0] if (p := z.flog.predicted(hours[i], 6)) else None) if i <= window.now_index else None
                for i in range(n)
            ]
            zones.append(
                ZoneViewInput(
                    id=sid, name=z.title, heat_type=z.cfg.get(CONF_HEAT_TYPE, "fbh"),
                    leads=bool(z.cfg.get(CONF_LEADS_RELEASE, True)), measured=measured,
                    comfort_low=[zone_comfort(z, h) for h in hours], group_labels=group_labels,
                    contrib_past=contrib_past, forecast6=forecast6,
                )
            )
        zones.sort(key=lambda zv: (not zv.leads, zv.name.lower()))

        return build_view(
            window=window, tz=tz, generated_at=now, t_out_measured=t_meas, t_out_forecast=t_fc, irr=irr,
            heating_actual=heating_actual, release=release, planner=planner, zones=zones, outlook=outlook,
            z=zval, decision=self._decision(now, data), plan_change=change, events=self._events(), errors=errors,
            dhw=fraction_of(dhw_eid, binary_value) if dhw_eid else None,
        )
