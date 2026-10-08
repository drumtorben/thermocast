"""Assemble the panel view next to the control path (never inside it)."""
from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from statistics import fmean
from typing import TYPE_CHECKING, Any

from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ANTI_CYCLE_MIN,
    CONF_BT_CONTROL,
    CONF_BT_ENTITY,
    CONF_BURNER_STARTS,
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
    CONF_STARTS_WEIGHT,
    CONF_TEMP_SENSORS,
    DEFAULT_ANTI_CYCLE_MIN,
    DEFAULT_CONFIDENCE_Z,
    DEFAULT_MIN_BLOCK_H,
    DEFAULT_STARTS_WEIGHT,
    DOMAIN,
)
from .core.bt import round_target
from .core.explain import PlanSnapshot, plan_change, snapshot_from
from .core.model import Prediction
from .core.planner import charge_cost
from .core.quality import hindcast
from .core.rollout import ActuatorState, block_lengths_for
from .core.rules import apply_rules, binary_value, release_state
from .core.series import hourly_fraction, hourly_increase, hourly_mean, mask_off
from .core.view import ZoneViewInput, align, build_view, compute_outlook, make_window
from .history import async_fetch_attribute, async_fetch_states

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
        self._window_start: datetime | None = None
        self._window_end: datetime | None = None
        self._snapshots: dict[datetime, PlanSnapshot] = {}
        # shadow actuator: in observe mode the real actuator never switches, so the rollout would
        # start without block/pause history. The shadow applies the same rules virtually.
        self._shadow_on = False
        self._shadow_change: datetime | None = None
        self._shadow_switches = 0
        self._shadow_day = None
        # the full rebuild (recorder, rollout, hindcast) runs in the background: neither the control path nor a
        # (re)load waits for it; the panel gets the result through these listeners
        self._listeners: list[Callable[[], None]] = []
        self._build_task: asyncio.Task | None = None
        self._pending: tuple[datetime, ThermocastData] | None = None
        self._unloaded = False

    @property
    def shadow_on(self) -> bool:
        """Observe mode: would the release be on now (same rules as the real actuator)?"""
        return self._shadow_on

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Called whenever the view changed (websocket subscription). Returns the remove function."""
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def mark_unloaded(self) -> None:
        self._unloaded = True
        self._pending = None
        if self._build_task is not None and not self._build_task.done():
            self._build_task.cancel()
        self.view = {"error": "unloaded"}
        self._notify()  # open panels re-subscribe

    def _update_shadow(self, now: datetime, data: ThermocastData) -> None:
        today = dt_util.as_local(now).date()
        if self._shadow_day != today:
            self._shadow_day, self._shadow_switches = today, 0
        if data.failsafe_reason:
            return  # a fail-safe allows heating but is no planned block (the real actuator owes no minimum block)
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

    @callback
    def async_schedule_refresh(self, now: datetime, data: ThermocastData) -> None:
        """After every coordinator update: decision + events right away, the full view (rollout, candidates,
        history) rebuilt in the background – so the panel always shows what the controller just planned
        (a rebuild takes well under a second since the planner is vectorised). Never raises."""
        if self._unloaded:
            return
        c = self._c
        try:
            self._update_shadow(now, data)
            if self.view is not None and "error" not in self.view:
                self._refresh_decision(now, data)
                self._notify()
        except Exception as err:
            _LOGGER.exception("Thermocast: refreshing the panel view failed")
            self.view = {"error": f"{type(err).__name__}: {err}"}
            self._notify()
            return
        self._pending = (now, data)  # updates arriving during a build: only the latest is built afterwards
        if self._build_task is None or self._build_task.done():
            self._build_task = c.config_entry.async_create_background_task(
                c.hass, self._async_build_pending(), "thermocast_view"
            )

    def _refresh_decision(self, now: datetime, data: ThermocastData) -> None:
        assert self.view is not None
        self.view = {
            **self.view,
            "generated_at": now.isoformat(),
            "decision": self._decision(now, data),
            "events": self._events(),
        }

    async def _async_build_pending(self) -> None:
        """Build the latest requested view; an update that arrives meanwhile is built right after."""
        while self._pending is not None and not self._unloaded:
            now, data = self._pending
            self._pending = None
            try:
                view = await self._async_build(now, now.replace(minute=0, second=0, microsecond=0), data)
                if self._unloaded:
                    return
                self.view = view
            except Exception as err:
                _LOGGER.exception("Thermocast: building the panel view failed")
                self.view = {"error": f"{type(err).__name__}: {err}"}
            self._notify()

    # ------------------------------------------------------------------ parts
    def _decision(self, now: datetime, data: ThermocastData) -> dict[str, Any]:
        a = self._c.actuator
        rules = a.rules
        options = self._c.config_entry.options
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
                "starts_weight": float(options.get(CONF_STARTS_WEIGHT, DEFAULT_STARTS_WEIGHT)),
                "anti_cycle_min": float(options.get(CONF_ANTI_CYCLE_MIN, DEFAULT_ANTI_CYCLE_MIN)),
            },
            "since_last_change_min": round(a.elapsed_h(now) * 60) if a.last_change else None,
            "forecast_age_min": _round(data.forecast_age_min, 0),
            "release_entity": a.entity_id,
            "release_state": a.entity_is_on(),
            "bt": data.bt,  # Better Thermostat target/reason per controlled zone
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
        from .coordinator import (
            build_plan_inputs,
            starts_weight,
            zone_base,
            zone_charge_cap,
            zone_comfort,
            zone_floor,
            zone_high,
            zone_quiet,
            zone_window_open,
        )

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
        starts_eid = c.config_entry.options.get(CONF_BURNER_STARTS)
        wanted = {e for lst in sensors.values() for e in lst} | {outdoor, pump, rel, planner_eid, dhw_eid, starts_eid}

        states = await async_fetch_states(hass, {e for e in wanted if e}, hours[0], now)
        if states is None:
            errors.append("no_recorder")
            states = {}
        bt_ids = {z.cfg[CONF_BT_ENTITY] for z in c.zones.values() if z.cfg.get(CONF_BT_CONTROL) and z.cfg.get(CONF_BT_ENTITY)}
        bt_hist = await async_fetch_attribute(hass, bt_ids, "temperature", hours[0], now) or {}

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
        # the boiler pump also runs for hot water: that time is not space heating
        pump_states = states.get(pump, []) if pump else []
        if dhw_eid and pump_states:
            pump_states = mask_off(pump_states, states.get(dhw_eid, []), binary_value)
        heating_actual = hourly_fraction(pump_states, hours, now, binary_value) if pump else [None] * n
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
                        z=zval, block_lengths=lengths, cost_fn=charge_cost(starts_weight(c.config_entry.options)),
                    )
                )
                snap = snapshot_from(outlook.rollout.first, outlook.first_inputs, hour0)
                self._snapshots = {h: s for h, s in self._snapshots.items() if h >= hour0 - HOUR}
                self._snapshots[hour0] = snap
                change = plan_change(self._snapshots.get(hour0 - HOUR), snap, {zi.name: zi.temp_now for zi in inputs})
                # operational forecast log (model tab: forecast quality per horizon) – the first plan of the hour:
                # a rebuild at :45 would turn the "1 h ahead" forecast into a 15-min one
                for zid, tr in outlook.rollout.zones.items():
                    if zid in c.zones and not c.zones[zid].flog.has(hour0):
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
            quiet = [zone_quiet(z, h) for h in hours]
            floor = [zone_floor(z, h) for h in hours]
            bt_on = bt_off = bt_past = []
            bt_eid = z.cfg.get(CONF_BT_ENTITY) if z.cfg.get(CONF_BT_CONTROL) else None
            if bt_eid:
                bt_on = [round_target(cap) if (cap := zone_charge_cap(z, h)) is not None else None for h in hours]
                bt_off = [round_target(zone_base(z) if q else f) for q, f in zip(quiet, floor)]
                if zone_window_open(hass, z):  # same as the actuator: the base while airing (from now on)
                    base = round_target(zone_base(z))
                    bt_on = bt_on[: window.now_index] + [base] * (n - window.now_index)
                    bt_off = bt_off[: window.now_index] + [base] * (n - window.now_index)
                bt_past = hourly_mean(bt_hist.get(bt_eid, []), hours, now)
            zones.append(
                ZoneViewInput(
                    id=sid, name=z.title, heat_type=z.cfg.get(CONF_HEAT_TYPE, "fbh"),
                    leads=bool(z.cfg.get(CONF_LEADS_RELEASE, True)), measured=measured,
                    comfort_low=[zone_comfort(z, h) for h in hours], group_labels=group_labels,
                    contrib_past=contrib_past, forecast6=forecast6,
                    comfort_high=[zone_high(z)] * n, floor=floor, quiet=quiet, bt_control=bool(bt_eid),
                    bt_on=bt_on, bt_off=bt_off, bt_past=bt_past,
                )
            )
        zones.sort(key=lambda zv: (not zv.leads, zv.name.lower()))

        return build_view(
            window=window, tz=tz, generated_at=now, t_out_measured=t_meas, t_out_forecast=t_fc, irr=irr,
            heating_actual=heating_actual, release=release, planner=planner, zones=zones, outlook=outlook,
            z=zval, decision=self._decision(now, data), plan_change=change, events=self._events(), errors=errors,
            dhw=fraction_of(dhw_eid, binary_value) if dhw_eid else None,
            burner_starts=hourly_increase(states.get(starts_eid, []), hours, now) if starts_eid else None,
        )
