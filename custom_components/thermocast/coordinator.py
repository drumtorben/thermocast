"""Coordinator: read sensors, learn online, forecast, plan, actuate."""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from functools import partial
from statistics import fmean
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .actuator import Actuator
from .const import (
    CALIBRATION_DAYS,
    CONF_ACTIVE_FROM,
    CONF_ACTIVE_TO,
    CONF_BASE_TEMP,
    CONF_BT_CONTROL,
    CONF_CALIBRATE_SIGMA,
    CONF_COMFORT_BAND,
    CONF_COMFORT_HIGH,
    CONF_COMFORT_SCHEDULE,
    CONF_COMFORT_TEMP,
    CONF_CONFIDENCE_Z,
    CONF_DHW_ENTITY,
    CONF_FLOW_TEMP_SENSOR,
    CONF_FORGETTING,
    CONF_GAIN_ENTITIES,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_LEADS_RELEASE,
    CONF_MIN_BLOCK_H,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_QUIET_FROM,
    CONF_QUIET_TO,
    CONF_STARTS_WEIGHT,
    CONF_SURFACES,
    CONF_TEMP_SENSORS,
    CONF_VALVE_ENTITY,
    CONF_WINDOW_ENTITIES,
    DEFAULT_BASE_OFFSET,
    DEFAULT_CALIBRATE_SIGMA,
    DEFAULT_CONFIDENCE_Z,
    DEFAULT_FORGETTING,
    DEFAULT_HIGH_OFFSET,
    DEFAULT_MIN_BLOCK_H,
    DEFAULT_Q_ON,
    DEFAULT_STARTS_WEIGHT,
    DOMAIN,
    FORECAST_MAX_AGE,
    FORECAST_STALE_FAILSAFE,
    HORIZON_HOURS,
    STORAGE_VERSION,
    SUBENTRY_ZONE,
    UPDATE_INTERVAL,
)
from .core.forecast import Forecast, fetch_forecast
from .core.model import HourRecord, OnlineZoneModel, SurfaceSpec, ZoneSpec
from .core.planner import PlanResult, ZonePlanInput, charge_cost, plan
from .core.quality import ForecastLog, RingLog, calibration, measured_by_hour, param_snapshot, std_scale_profile
from .core.rollout import block_lengths_for
from .core.rules import binary_value, hvac_heating
from .events import EventLog
from .view_builder import ViewBuilder
from .zone_actuator import ZoneActuator

_LOGGER = logging.getLogger(__name__)
PARAM_HISTORY_DAYS = 30
SAVE_DELAY_S = 60
ISSUE_FAILSAFE = "failsafe"
FAILSAFE_ISSUE_AFTER = timedelta(hours=1)


# --------------------------------------------------------------------- helpers
def _num(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    st = hass.states.get(entity_id)
    if st is None:
        return None
    try:
        v = float(st.state)
    except (TypeError, ValueError):
        return binary_value(st.state)  # on/off, true/false, an/aus … -> 1.0 / 0.0
    return v if math.isfinite(v) else None


def _is_on(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    st = hass.states.get(entity_id) if entity_id else None
    v = binary_value(st.state) if st is not None else None
    return None if v is None else v > 0


def _valve_share(hass: HomeAssistant, entity_id: str) -> float | None:
    """Radiator valve 0..1: a thermostat's ``hvac_action`` (heating/idle) or an opening in %."""
    if entity_id.startswith("climate."):
        st = hass.states.get(entity_id)
        return hvac_heating(st.attributes.get("hvac_action")) if st is not None else None
    v = _num(hass, entity_id)
    return None if v is None else min(1.0, max(0.0, v / 100.0))


def _or(value: float | None, default: float | None) -> float | None:
    """``value`` unless it is missing – unlike ``value or default`` this keeps 0.0."""
    return default if value is None else value


def _mean(values: list[float]) -> float | None:
    vals = [v for v in values if v is not None and math.isfinite(v)]
    return fmean(vals) if vals else None


def _column(rows: list[list[float | None]], i: int) -> list[float | None]:
    """Column ``i`` of the per-sample rows; rows from another sensor layout simply lack it."""
    return [row[i] if i < len(row) else None for row in rows]


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _in_window(local: datetime, start: time, end: time) -> bool:
    t = local.time()
    return start <= t < end if start <= end else (t >= start or t < end)


# ------------------------------------------------------------------- runtime
@dataclass
class ZoneRuntime:
    subentry_id: str
    title: str
    cfg: dict[str, Any]
    model: OnlineZoneModel
    pending_hour: datetime | None = None
    pending_temp: float | None = None
    acc: dict[str, list] = field(default_factory=dict)
    q_on: float = DEFAULT_Q_ON
    log: RingLog = field(default_factory=RingLog)  # closed hours: inputs, measurement, a-priori error
    flog: ForecastLog = field(default_factory=ForecastLog)  # operational predictions (rollout)
    params: list[dict[str, Any]] = field(default_factory=list)  # daily parameter snapshots
    std_scale: tuple[float, ...] = ()  # calibrated σ factor per hour ahead (from the forecast log)
    schedule_plan: dict[str, list[dict[str, Any]]] | None = None  # weekly plan of the comfort schedule

    def reset_acc(self) -> None:
        self.acc = {"t_out": [], "q": [], "neighbors": [], "gains": [], "window": []}


@dataclass
class ZoneResult:
    temp: float | None
    times: list[datetime] = field(default_factory=list)
    mean: list[float] = field(default_factory=list)
    lower: list[float] = field(default_factory=list)
    comfort_low: list[float | None] = field(default_factory=list)
    min_lower: float | None = None
    demand: bool = False
    mae: float = 0.0
    n_updates: int = 0
    solar: dict[str, float] = field(default_factory=dict)
    params: dict[str, float] = field(default_factory=dict)


@dataclass
class ThermocastData:
    zones: dict[str, ZoneResult]
    want_heat: bool
    release_on: bool
    control_enabled: bool
    block_start: datetime | None
    block_end: datetime | None
    failsafe_reason: str | None
    forecast_age_min: float | None
    override: str | None = None  # observe | failsafe | min_block | min_pause | budget
    planner_heat: bool | None = None  # raw planner wish (before fail-safe)
    bt: dict[str, dict[str, Any]] = field(default_factory=dict)  # Better Thermostat target per controlled zone


WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _as_time(value: Any) -> time:
    return value if isinstance(value, time) else time.fromisoformat(str(value))


def _in_schedule(local: datetime, plan: dict[str, list[dict[str, Any]]]) -> bool:
    t = local.time()
    for block in plan.get(WEEKDAYS[local.weekday()], []):
        start, end = _as_time(block["from"]), _as_time(block["to"])
        if start <= t and (t < end or end == time.max or end == time(0)):
            return True
    return False


def zone_comfort(z: ZoneRuntime, when: datetime) -> float | None:
    """Lower comfort bound at ``when`` (None outside comfort). A schedule helper, if configured and
    readable, replaces the fixed from/until window."""
    local = dt_util.as_local(when)
    if z.schedule_plan is not None:
        active = _in_schedule(local, z.schedule_plan)
    else:
        active = _in_window(local, _parse_time(z.cfg[CONF_ACTIVE_FROM]), _parse_time(z.cfg[CONF_ACTIVE_TO]))
    if not active:
        return None
    return float(z.cfg[CONF_COMFORT_TEMP]) - float(z.cfg[CONF_COMFORT_BAND])


def zone_high(z: ZoneRuntime) -> float:
    """Upper bound: a block may charge the zone up to here (default comfort + 1 K)."""
    value = z.cfg.get(CONF_COMFORT_HIGH)
    return float(value) if value is not None else float(z.cfg[CONF_COMFORT_TEMP]) + DEFAULT_HIGH_OFFSET


def zone_base(z: ZoneRuntime) -> float:
    """Lower bound outside comfort time (default comfort − 2 K)."""
    value = z.cfg.get(CONF_BASE_TEMP)
    return float(value) if value is not None else float(z.cfg[CONF_COMFORT_TEMP]) - DEFAULT_BASE_OFFSET


def zone_floor(z: ZoneRuntime, when: datetime) -> float:
    """Effective lower bound for the planner: comfort − band in comfort time, otherwise the base temperature."""
    low = zone_comfort(z, when)
    return low if low is not None else zone_base(z)


def zone_quiet(z: ZoneRuntime, when: datetime, lead: timedelta = timedelta(0)) -> bool:
    """Inside the zone's quiet time (no thermostat writes); ``lead`` looks ahead (set the floor in time)."""
    start, end = z.cfg.get(CONF_QUIET_FROM), z.cfg.get(CONF_QUIET_TO)
    if not start or not end:
        return False
    return _in_window(dt_util.as_local(when + lead), _parse_time(start), _parse_time(end))


def zone_charge_cap(z: ZoneRuntime, when: datetime) -> float | None:
    """Where the zone's thermostat closes during a block: the upper bound, in quiet time the base temperature.
    None = no thermostat control (the zone takes what it gets)."""
    if not z.cfg.get(CONF_BT_CONTROL):
        return None
    return zone_base(z) if zone_quiet(z, when) else zone_high(z)


def build_plan_inputs(
    hass: HomeAssistant, zones: dict[str, ZoneRuntime], fc: Forecast, idx0: int, horizon: int
) -> list[ZonePlanInput]:
    """Plan inputs from forecast index ``idx0`` for ``horizon`` hours (zones without temperature are left out).

    Prediction i refers to the end of hour i, i.e. ``fc.times[idx0 + 1 + i]`` (comfort is evaluated there).
    """
    horizon = max(0, min(horizon, len(fc.times) - idx0 - 1))
    inputs: list[ZonePlanInput] = []
    for sid, z in zones.items():
        temp = _mean([_num(hass, e) for e in z.cfg.get(CONF_TEMP_SENSORS, [])])
        if temp is None:
            continue
        neighbors = tuple(
            (v if (v := _num(hass, e)) is not None else temp) for e in z.cfg.get(CONF_NEIGHBOR_SENSORS, [])
        )
        gains = tuple(_or(_num(hass, e), 0.0) for e in z.cfg.get(CONF_GAIN_ENTITIES, []))
        future = [
            HourRecord(
                temp=temp, t_out=fc.t_out[idx0 + h], irr=fc.irr_at(idx0 + h), q=0.0, neighbors=neighbors, gains=gains
            )
            for h in range(horizon)
        ]
        ends = [fc.times[idx0 + 1 + h] for h in range(horizon)]  # prediction h = end of hour h
        starts = [fc.times[idx0 + h] for h in range(horizon)]  # heat input of hour h
        high = zone_high(z)
        inputs.append(
            ZonePlanInput(
                name=sid, model=z.model, temp_now=temp, future=future,
                comfort_low=[zone_floor(z, t) for t in ends],
                comfort_high=[high] * horizon,
                charge_cap=[zone_charge_cap(z, t) for t in starts],
                q_on=z.q_on, leads_release=bool(z.cfg.get(CONF_LEADS_RELEASE, True)), std_scale=z.std_scale,
            )
        )
    return inputs


def starts_weight(options: dict[str, Any]) -> float:
    """The starts-vs-gas option (0…100) as planner weight 0…1."""
    return float(options.get(CONF_STARTS_WEIGHT, DEFAULT_STARTS_WEIGHT)) / 100.0


class ThermocastCoordinator(DataUpdateCoordinator[ThermocastData]):
    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL)
        self.store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.logs_store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}.logs")
        self.actuator = Actuator(hass, entry)
        self.zones: dict[str, ZoneRuntime] = {}
        self.events = EventLog()
        self.zone_actuator = ZoneActuator(hass, self.events)
        self.forecast: Forecast | None = None
        self.control_enabled = False  # observe mode by default
        self.house_device_id: str | None = None  # set in async_setup_entry
        self._failsafe_active = False
        self.view_builder = ViewBuilder(self)
        self.control_since: datetime | None = None
        self._warmstart_running = False
        self._failsafe_since: datetime | None = None
        self._failsafe_issue = False
        self._schedules_hour: datetime | None = None
        self.actuator.on_write = lambda on: self.events.add(
            dt_util.utcnow(), "release_written", detail="on" if on else "off"
        )
        self.actuator.on_event = lambda type_, detail: self.events.add(dt_util.utcnow(), type_, detail=detail)

    # ------------------------------------------------------------- lifecycle
    async def async_load(self) -> None:
        stored = await self.store.async_load() or {}
        logs = (await self.logs_store.async_load() or {}).get("zones", {})
        self.control_enabled = bool(stored.get("control_enabled", False))
        self.actuator.load(stored.get("actuator", {}))
        self.zone_actuator.load(stored.get("bt", {}))
        self.events.load(stored.get("events", []))
        # an open fail-safe from before the restart/reload is closed by the next good update
        last = next((e for e in reversed(self.events.to_list()) if e["type"].startswith("failsafe_")), None)
        self._failsafe_active = bool(last and last["type"] == "failsafe_start")
        forgetting = float(self.config_entry.options.get(CONF_FORGETTING, DEFAULT_FORGETTING))
        models = stored.get("zones", {})
        for subentry in self.config_entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_ZONE:
                continue
            zr = self._build_zone(subentry, forgetting)
            saved = models.get(subentry.subentry_id)
            if saved:
                same_layout = zr.model.load_dict(saved.get("model", {}))
                if not same_layout:
                    _LOGGER.info("Zone %s: configuration changed, model restarts from prior", subentry.title)
                    self.events.add(dt_util.utcnow(), "model_reset", zone=subentry.subentry_id)
                zr.q_on = float(saved.get("q_on", DEFAULT_Q_ON))
                pending = saved.get("pending") or {}
                # the running hour was collected with the old sensor layout -> only keep it if nothing changed
                if pending.get("hour") and same_layout:
                    # the running hour survives a restart; _update decides whether it can still be learned
                    zr.pending_hour = datetime.fromisoformat(pending["hour"])
                    zr.pending_temp = pending.get("temp")
                    zr.acc = {k: list(v) for k, v in pending.get("acc", {}).items()} or zr.acc
            zlog = logs.get(subentry.subentry_id) or saved or {}  # logs lived in the main store in 0.3.0
            zr.log.load(zlog.get("log", []))
            zr.flog.load(zlog.get("flog", {}))
            zr.params = list(zlog.get("params", []))[-PARAM_HISTORY_DAYS:]
            self._recalibrate(zr)
            self.zones[subentry.subentry_id] = zr
        cs = stored.get("control_since")
        self.control_since = datetime.fromisoformat(cs) if cs else None

    def _update_failsafe_issue(self, now: datetime, failsafe: str | None) -> None:
        """Repair issue only for a fail-safe that lasts (a single failed forecast fetch is not news)."""
        if not failsafe:
            if self._failsafe_issue:
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_FAILSAFE)
                self._failsafe_issue = False
            self._failsafe_since = None
            return
        since = self._failsafe_since or now
        self._failsafe_since = since
        if not self._failsafe_issue and now - since >= FAILSAFE_ISSUE_AFTER:
            ir.async_create_issue(
                self.hass, DOMAIN, ISSUE_FAILSAFE, is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_FAILSAFE, translation_placeholders={"reason": failsafe},
            )
            self._failsafe_issue = True

    def _recalibrate(self, z: ZoneRuntime) -> None:
        """σ factors from the last two weeks of forecast checks (option, on by default)."""
        if not self.config_entry.options.get(CONF_CALIBRATE_SIGMA, DEFAULT_CALIBRATE_SIGMA):
            z.std_scale = ()
            return
        since = dt_util.utcnow() - timedelta(days=CALIBRATION_DAYS)
        try:
            factors = calibration(z.flog, measured_by_hour(z.log.to_list()), since)
        except Exception:  # diagnostics must never keep the integration from starting
            _LOGGER.exception("Thermocast: σ calibration failed for zone %s, using the raw σ", z.title)
            factors = {}
        z.std_scale = std_scale_profile(factors)

    def _build_zone(self, sub: ConfigSubentry, forgetting: float) -> ZoneRuntime:
        cfg = dict(sub.data)
        spec = ZoneSpec(
            heat_type=cfg.get(CONF_HEAT_TYPE, "fbh"),
            surfaces=tuple(SurfaceSpec.from_dict(s) for s in cfg.get(CONF_SURFACES, [])),
            n_neighbors=len(cfg.get(CONF_NEIGHBOR_SENSORS, [])),
            n_gains=len(cfg.get(CONF_GAIN_ENTITIES, [])),
        )
        zr = ZoneRuntime(sub.subentry_id, sub.title, cfg, OnlineZoneModel(spec, forgetting=forgetting))
        zr.reset_acc()
        return zr

    # Two stores: the small state (models, actuator, events, running hour) is saved after every update
    # (debounced); the large logs (hour log, forecast log, parameter history) only when an hour closes.
    def _state_data(self) -> dict[str, Any]:
        return {
            "control_enabled": self.control_enabled,
            "actuator": self.actuator.to_dict(),
            "bt": self.zone_actuator.to_dict(),
            "events": self.events.to_list(),
            "control_since": self.control_since.isoformat() if self.control_since else None,
            "zones": {
                sid: {
                    "model": z.model.to_dict(),
                    "q_on": z.q_on,
                    "pending": {
                        "hour": z.pending_hour.isoformat() if z.pending_hour else None,
                        "temp": z.pending_temp,
                        "acc": z.acc,
                    },
                }
                for sid, z in self.zones.items()
            },
        }

    def _logs_data(self) -> dict[str, Any]:
        return {
            "zones": {
                sid: {"log": z.log.to_list(), "flog": z.flog.to_dict(), "params": z.params}
                for sid, z in self.zones.items()
            }
        }

    async def async_save(self) -> None:
        """Save both stores now (control changes, unload)."""
        await self.store.async_save(self._state_data())
        await self.logs_store.async_save(self._logs_data())
        self.events.dirty = False

    def _schedule_save(self, logs: bool) -> None:
        self.store.async_delay_save(self._state_data, SAVE_DELAY_S)
        if logs:
            self.logs_store.async_delay_save(self._logs_data, SAVE_DELAY_S)
        self.events.dirty = False

    async def async_set_control(self, enabled: bool) -> None:
        self.control_enabled = enabled
        self.events.add(dt_util.utcnow(), "control_on" if enabled else "control_off")
        if enabled and self.control_since is None:
            self.control_since = dt_util.utcnow()  # KPI before/after split
        if not enabled:
            await self.actuator.async_force_on()  # hand control back: heating allowed
            await self.zone_actuator.async_failsafe(self.zones, self.zone_temps())  # rooms at their lower bound
        await self.async_save()
        await self.async_request_refresh()

    def zone_temps(self) -> dict[str, float | None]:
        return {sid: _mean([_num(self.hass, e) for e in z.cfg.get(CONF_TEMP_SENSORS, [])]) for sid, z in self.zones.items()}

    async def async_warmstart(self, only_fresh: bool) -> dict[str, int]:
        """Learn zone models from recorder history (see warmstart.py). Never raises."""
        from .warmstart import async_warmstart

        if self._warmstart_running:
            return {}
        self._warmstart_running = True
        try:
            result = await async_warmstart(self, only_fresh)
        except Exception:
            _LOGGER.exception("Thermocast warm start failed")
            return {}
        finally:
            self._warmstart_running = False
        if any(result.values()):
            await self.async_save()
            self.view_builder.invalidate()
            await self.async_request_refresh()
        return result

    @property
    def view(self) -> dict[str, Any] | None:
        """Panel view (contract v1, see core/view.py) – read by the websocket API."""
        return self.view_builder.view

    async def async_shutdown_failsafe(self) -> None:
        if self.control_enabled:
            await self.actuator.async_force_on()
            await self.zone_actuator.async_failsafe(self.zones, self.zone_temps())
        await self.async_save()
        self.view_builder.mark_unloaded()
        ir.async_delete_issue(self.hass, DOMAIN, ISSUE_FAILSAFE)  # a removed integration has nothing to repair
        self.async_update_listeners()  # open panels re-subscribe

    # ---------------------------------------------------------------- update
    async def _async_update_data(self) -> ThermocastData:
        try:
            return await self._update()
        except Exception as err:
            _LOGGER.exception("Thermocast update failed – releasing heating")
            if not self._failsafe_active:
                self.events.add(dt_util.utcnow(), "failsafe_start", detail="update_error")
                self._failsafe_active = True
            self._update_failsafe_issue(dt_util.utcnow(), "update_error")
            if self.control_enabled:
                await self.actuator.async_force_on()
            raise UpdateFailed(str(err)) from err

    async def _update(self) -> ThermocastData:
        hass = self.hass
        now = dt_util.utcnow()
        hour = now.replace(minute=0, second=0, microsecond=0)
        data = self.config_entry.data

        await self._ensure_forecast(now)
        await self._ensure_schedules(hour)

        t_out = _num(hass, data.get(CONF_OUTDOOR_SENSOR))
        if t_out is None and self.forecast:
            idx = self.forecast.index_of(now)
            t_out = self.forecast.t_out[idx] if idx is not None else None
        flow = _num(hass, data.get(CONF_FLOW_TEMP_SENSOR))
        heating = _is_on(hass, data.get(CONF_HEATING_ACTIVE)) if data.get(CONF_HEATING_ACTIVE) else None
        if heating is None and flow is not None and not data.get(CONF_HEATING_ACTIVE):
            # no 'heating active' entity configured: rely on flow temperature only
            # (less accurate – DHW charging also raises the boiler flow temperature)
            heating = True
        if _is_on(hass, self.config_entry.options.get(CONF_DHW_ENTITY)):
            heating = False  # combi boiler: hot water charging runs the same pump with a hot flow

        hour_closed = False
        for z in self.zones.values():
            temp = _mean([_num(hass, e) for e in z.cfg.get(CONF_TEMP_SENSORS, [])])
            if temp is None:
                continue
            # 1) close the previous hour -> one RLS step
            if z.pending_hour is not None and hour > z.pending_hour:
                try:
                    self._close_hour(z, temp, consecutive=(hour - z.pending_hour) == timedelta(hours=1))
                except Exception:  # lose this hour, never get stuck on it (it would fail again every 15 min)
                    _LOGGER.exception("Thermocast: closing the hour failed for zone %s, skipping it", z.title)
                hour_closed = True
            if z.pending_hour is None or hour > z.pending_hour:
                z.pending_hour, z.pending_temp = hour, temp
                z.reset_acc()
            # 2) accumulate inputs of the running hour
            q = 0.0
            if heating and flow is not None:
                q = max(0.0, flow - temp)
                if z.cfg.get(CONF_HEAT_TYPE) == "radiator" and z.cfg.get(CONF_VALVE_ENTITY):
                    valve = _valve_share(hass, z.cfg[CONF_VALVE_ENTITY])
                    q *= valve if valve is not None else 0.0
            z.acc["t_out"].append(t_out)
            z.acc["q"].append(q)
            z.acc["neighbors"].append([_num(hass, e) for e in z.cfg.get(CONF_NEIGHBOR_SENSORS, [])])
            z.acc["gains"].append([_or(_num(hass, e), 0.0) for e in z.cfg.get(CONF_GAIN_ENTITIES, [])])
            z.acc["window"].append(any(_is_on(hass, e) for e in z.cfg.get(CONF_WINDOW_ENTITIES, [])))

        result = await self._forecast_and_plan(now)
        failsafe = result.pop("failsafe")
        if failsafe and not self._failsafe_active:
            self.events.add(now, "failsafe_start", detail=failsafe)
            self._failsafe_since = now
        elif not failsafe and self._failsafe_active:
            self.events.add(now, "failsafe_end")
        self._failsafe_active = bool(failsafe)
        self._update_failsafe_issue(now, failsafe)
        want_heat = True if failsafe else result["want_heat"]
        release, override = await self.actuator.async_apply(want_heat, self.control_enabled, now, forced=bool(failsafe))
        if failsafe:
            override = "failsafe"
        bt = await self.zone_actuator.async_apply(
            self.zones, self.zone_temps(), block_on=release, failsafe=bool(failsafe), enabled=self.control_enabled,
            now=now, block_end=result["block_end"],
        )
        age = (now - self.forecast.fetched_at).total_seconds() / 60 if self.forecast and self.forecast.fetched_at else None
        self._schedule_save(logs=hour_closed)  # debounced; also keeps the running hour across restarts
        data_out = ThermocastData(
            zones=result["zones"],
            want_heat=want_heat,
            release_on=release,
            control_enabled=self.control_enabled,
            block_start=result["block_start"],
            block_end=result["block_end"],
            failsafe_reason=failsafe,
            forecast_age_min=age,
            override=override,
            planner_heat=result["want_heat"],
            bt=bt,
        )
        await self.view_builder.async_refresh(now, data_out)  # never raises
        return data_out

    def _close_hour(self, z: ZoneRuntime, temp_now: float, consecutive: bool) -> None:
        acc = z.acc
        n_nb = len(z.cfg.get(CONF_NEIGHBOR_SENSORS, []))
        n_g = len(z.cfg.get(CONF_GAIN_ENTITIES, []))
        neighbors = tuple(_or(_mean(_column(acc["neighbors"], i)), z.pending_temp) for i in range(n_nb))
        gains = tuple(_or(_mean(_column(acc["gains"], i)), 0.0) for i in range(n_g))
        irr: dict[str, float] = {}
        if self.forecast and z.pending_hour is not None:
            idx = self.forecast.index_of(z.pending_hour)
            if idx is not None:
                irr = self.forecast.irr_at(idx)
        t_out = _mean(acc["t_out"])
        q = _or(_mean(acc["q"]), 0.0)
        valid = consecutive and t_out is not None and not any(acc["window"]) and bool(irr or not z.model.spec.surfaces)
        if any(acc["window"]):
            self.events.add(dt_util.utcnow(), "window_open", zone=z.subentry_id, dedupe=timedelta(minutes=59))
        rec = HourRecord(
            temp=z.pending_temp if z.pending_temp is not None else temp_now,
            t_out=t_out if t_out is not None else float("nan"),
            irr=irr,
            q=q,
            neighbors=neighbors,
            gains=gains,
            valid=valid,
        )
        err = z.model.update(rec, temp_now)
        if q > 1.0:  # learn the typical heating proxy during blocks
            z.q_on = 0.95 * z.q_on + 0.05 * q
        if z.pending_hour is not None:
            z.log.append(
                {"t": z.pending_hour.isoformat(), "rec": rec.to_dict(), "temp_next": temp_now, "err": err}
            )
        today = dt_util.as_local(dt_util.utcnow()).date().isoformat()
        if not z.params or z.params[-1]["date"] != today:
            z.params = [*z.params, {"date": today, **param_snapshot(z.model)}][-PARAM_HISTORY_DAYS:]
        self._recalibrate(z)

    async def _ensure_schedules(self, hour: datetime) -> None:
        """Read the weekly plans of the comfort schedules once per hour (they rarely change)."""
        ids = sorted({z.cfg[CONF_COMFORT_SCHEDULE] for z in self.zones.values() if z.cfg.get(CONF_COMFORT_SCHEDULE)})
        if not ids or self._schedules_hour == hour:
            return
        self._schedules_hour = hour
        plans: dict[str, Any] = {}
        try:
            plans = await self.hass.services.async_call(
                "schedule", "get_schedule", {"entity_id": ids}, blocking=True, return_response=True
            ) or {}
        except Exception as err:  # noqa: BLE001 - fall back to the fixed window
            _LOGGER.warning("Thermocast: comfort schedules unavailable (%s) – using the fixed comfort window", err)
        for z in self.zones.values():
            eid = z.cfg.get(CONF_COMFORT_SCHEDULE)
            z.schedule_plan = plans.get(eid) if eid else None

    async def _ensure_forecast(self, now: datetime) -> None:
        if self.forecast and self.forecast.fetched_at and now - self.forecast.fetched_at < FORECAST_MAX_AGE:
            return
        orientations: dict[str, tuple[str, float, float]] = {}
        for z in self.zones.values():
            for s in z.model.spec.surfaces:
                orientations[s.key] = (s.key, s.tilt, s.azimuth)
        try:
            self.forecast = await fetch_forecast(
                async_get_clientsession(self.hass),
                self.hass.config.latitude,
                self.hass.config.longitude,
                orientations.values(),
            )
        except Exception as err:  # noqa: BLE001 - keep old forecast, fail-safe handles staleness
            _LOGGER.warning("Open-Meteo forecast failed: %s", err)
            self.events.add(dt_util.utcnow(), "forecast_failed", detail=str(err)[:120], dedupe=timedelta(hours=1))

    async def _forecast_and_plan(self, now: datetime) -> dict[str, Any]:
        hass = self.hass
        zones_out: dict[str, ZoneResult] = {}
        fc = self.forecast
        if fc is None or fc.fetched_at is None or now - fc.fetched_at > FORECAST_STALE_FAILSAFE:
            for sid, z in self.zones.items():
                zones_out[sid] = ZoneResult(temp=z.pending_temp, mae=z.model.mae, n_updates=z.model.n_updates)
            return {"zones": zones_out, "want_heat": True, "failsafe": "no_forecast", "block_start": None, "block_end": None}

        idx0 = fc.index_of(now)
        if idx0 is None:
            return {"zones": zones_out, "want_heat": True, "failsafe": "forecast_gap", "block_start": None, "block_end": None}
        horizon = min(HORIZON_HOURS, len(fc.times) - idx0 - 1)
        times = [fc.times[idx0 + 1 + h] for h in range(horizon)]
        inputs = build_plan_inputs(hass, self.zones, fc, idx0, horizon)
        failsafe = None
        planned_ids = {zi.name for zi in inputs}
        for sid, z in self.zones.items():
            if sid not in planned_ids and z.cfg.get(CONF_LEADS_RELEASE, True):
                failsafe = f"no_temperature:{z.title}"
        for zi in inputs:
            z = self.zones[zi.name]
            zones_out[zi.name] = ZoneResult(
                temp=zi.temp_now, times=times, comfort_low=zi.comfort_low, mae=z.model.mae,
                n_updates=z.model.n_updates, solar=z.model.solar_response(), params=z.model.params(),
            )

        if not inputs:
            return {
                "zones": zones_out, "want_heat": True, "failsafe": failsafe or "no_zones",
                "block_start": None, "block_end": None,
            }

        zval = float(self.config_entry.options.get(CONF_CONFIDENCE_Z, DEFAULT_CONFIDENCE_Z))
        lengths = block_lengths_for(float(self.config_entry.options.get(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H)))
        # numpy work off the event loop
        running = self.actuator.commanded if self.control_enabled else self.view_builder.shadow_on
        result: PlanResult = await self.hass.async_add_executor_job(
            partial(
                plan, inputs, block_lengths=lengths, z=zval, cost_fn=charge_cost(starts_weight(self.config_entry.options)),
                running=bool(running),
            )
        )
        for zi in inputs:
            pred = result.free_run[zi.name]
            zr = zones_out[zi.name]
            zr.mean = [round(v, 2) for v in pred.mean]
            zr.lower = [round(v, 2) for v in pred.lower(zval)]
            relevant = [lb for lb, c in zip(zr.lower, zr.comfort_low) if c is not None]
            zr.min_lower = min(relevant) if relevant else (min(zr.lower) if zr.lower else None)
            zr.demand = result.violation_free.get(zi.name, 0.0) > 0

        block_start = block_end = None
        if result.best.start is not None:
            block_start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=result.best.start)
            block_end = block_start + timedelta(hours=result.best.length)
        return {
            "zones": zones_out,
            "want_heat": result.heat_now,
            "failsafe": failsafe,
            "block_start": block_start,
            "block_end": block_end,
        }
