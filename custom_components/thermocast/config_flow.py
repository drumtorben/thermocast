"""Config flow: house (main entry), zones (subentries) and options."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_FROM,
    CONF_ACTIVE_TO,
    CONF_BASE_TEMP,
    CONF_BT_CONTROL,
    CONF_BT_ENTITY,
    CONF_BURNER_STARTS,
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
    CONF_HEAT_ENERGY,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_LEADS_RELEASE,
    CONF_MAX_SWITCHES,
    CONF_MIN_BLOCK_H,
    CONF_MIN_PAUSE_H,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_QUIET_FROM,
    CONF_QUIET_TO,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
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
    DEFAULT_MAX_SWITCHES,
    DEFAULT_MIN_BLOCK_H,
    DEFAULT_MIN_PAUSE_H,
    DEFAULT_STARTS_WEIGHT,
    DOMAIN,
    HEAT_TYPES,
    SUBENTRY_ZONE,
)
from .core.model import SurfaceSpec

SELECT_DOMAINS = ("select", "input_select")
NUMBER_DOMAINS = ("number", "input_number")


def _temp_sensor(multiple: bool = False) -> selector.EntitySelector:
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="temperature", multiple=multiple)
    )


def _number(min_: float, max_: float, step: float | str, unit: str | None = None) -> selector.NumberSelector:
    config = selector.NumberSelectorConfig(min=min_, max=max_, step=step, mode=selector.NumberSelectorMode.BOX)
    if unit is not None:  # the selector schema rejects None
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(config)


HOUSE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default="Thermocast"): str,
        vol.Required(CONF_OUTDOOR_SENSOR): _temp_sensor(),
        vol.Required(CONF_FLOW_TEMP_SENSOR): _temp_sensor(),
        vol.Optional(CONF_HEATING_ACTIVE): selector.EntitySelector(
            selector.EntitySelectorConfig(domain=["binary_sensor", "switch", "sensor"])
        ),
        vol.Required(CONF_RELEASE_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain=["select", "input_select", "number", "input_number", "switch", "input_boolean"]
            )
        ),
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_MIN_BLOCK_H, default=options.get(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H)): _number(1, 8, 1, "h"),
            vol.Required(CONF_MIN_PAUSE_H, default=options.get(CONF_MIN_PAUSE_H, DEFAULT_MIN_PAUSE_H)): _number(0, 8, 1, "h"),
            vol.Required(
                CONF_MAX_SWITCHES, default=options.get(CONF_MAX_SWITCHES, DEFAULT_MAX_SWITCHES)
            ): _number(2, 48, 1),
            vol.Required(CONF_FORGETTING, default=options.get(CONF_FORGETTING, DEFAULT_FORGETTING)): _number(
                0.98, 0.9995, "any"
            ),
            vol.Required(
                CONF_CONFIDENCE_Z, default=options.get(CONF_CONFIDENCE_Z, DEFAULT_CONFIDENCE_Z)
            ): _number(0, 3, 0.1),
            vol.Required(
                CONF_CALIBRATE_SIGMA, default=options.get(CONF_CALIBRATE_SIGMA, DEFAULT_CALIBRATE_SIGMA)
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_STARTS_WEIGHT, default=options.get(CONF_STARTS_WEIGHT, DEFAULT_STARTS_WEIGHT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, step=5, mode=selector.NumberSelectorMode.SLIDER)
            ),
            # optional sources for the panel (DHW hatching, KPIs) – suggested, so they can be cleared
            vol.Optional(CONF_DHW_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["binary_sensor", "switch", "sensor"])
            ),
            vol.Optional(CONF_BURNER_STARTS): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Optional(CONF_HEAT_ENERGY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="energy")
            ),
        }
    )


def _zone_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_NAME): str,
            vol.Required(CONF_HEAT_TYPE, default="fbh"): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=HEAT_TYPES, translation_key=CONF_HEAT_TYPE, mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Required(CONF_TEMP_SENSORS): _temp_sensor(multiple=True),
            vol.Optional(CONF_VALVE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["climate", "sensor", "number"])
            ),
            vol.Required(CONF_COMFORT_TEMP, default=20.5): _number(10, 26, 0.1, "°C"),
            vol.Required(CONF_COMFORT_BAND, default=0.3): _number(0, 2, 0.1, "K"),
            vol.Required(CONF_ACTIVE_FROM, default="06:00:00"): selector.TimeSelector(),
            vol.Required(CONF_ACTIVE_TO, default="22:00:00"): selector.TimeSelector(),
            vol.Optional(CONF_COMFORT_SCHEDULE): selector.EntitySelector(selector.EntitySelectorConfig(domain="schedule")),
            vol.Required(CONF_LEADS_RELEASE, default=True): selector.BooleanSelector(),
            # charge and coast: optional – without them comfort + 1 K / comfort − 2 K apply
            vol.Optional(CONF_COMFORT_HIGH): _number(12, 26, 0.1, "°C"),
            vol.Optional(CONF_BASE_TEMP): _number(10, 24, 0.1, "°C"),
            vol.Optional(CONF_BT_CONTROL, default=False): selector.BooleanSelector(),
            vol.Optional(CONF_BT_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig(domain="climate")),
            vol.Optional(CONF_QUIET_FROM): selector.TimeSelector(),
            vol.Optional(CONF_QUIET_TO): selector.TimeSelector(),
            vol.Optional(CONF_SURFACES, default=[]): selector.ObjectSelector(),
            vol.Optional(CONF_NEIGHBOR_SENSORS, default=[]): _temp_sensor(multiple=True),
            vol.Optional(CONF_GAIN_ENTITIES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(multiple=True)
            ),
            vol.Optional(CONF_WINDOW_ENTITIES, default=[]): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="binary_sensor", multiple=True)
            ),
        }
    )


def _validate_surfaces(raw: Any) -> list[dict[str, Any]]:
    """Accept a YAML list like ``[{kind: window, azimuth: 90, tilt: 90}]``."""
    if raw in (None, "", {}):
        return []
    if not isinstance(raw, list):
        raise vol.Invalid("surfaces must be a list")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise vol.Invalid("each surface must be a mapping")
        spec = SurfaceSpec.from_dict(item)  # raises ValueError on nonsense
        out.append({"kind": spec.kind, "azimuth": spec.azimuth, "tilt": spec.tilt, "name": spec.name})
    return out


class ThermocastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Main entry = the house and its heat source."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            self._data = dict(user_input)
            domain = user_input[CONF_RELEASE_ENTITY].split(".")[0]
            if domain in SELECT_DOMAINS + NUMBER_DOMAINS:
                return await self.async_step_release_values()
            return self._create()
        return self.async_show_form(step_id="user", data_schema=HOUSE_SCHEMA)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change sensors or the release entity/values; zones and learned models are kept."""
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            self._data = dict(user_input)
            if user_input[CONF_RELEASE_ENTITY].split(".")[0] in SELECT_DOMAINS + NUMBER_DOMAINS:
                return await self.async_step_release_values()
            return await self._update()
        schema = self.add_suggested_values_to_schema(HOUSE_SCHEMA, {**entry.data, CONF_NAME: entry.title})
        return self.async_show_form(step_id="reconfigure", data_schema=schema)

    async def async_step_release_values(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """For select entities: which option means 'heating allowed' / 'blocked'."""
        errors: dict[str, str] = {}
        if user_input is not None:
            is_number = self._data[CONF_RELEASE_ENTITY].split(".")[0] in NUMBER_DOMAINS
            try:
                if is_number:
                    float(user_input[CONF_RELEASE_ON]), float(user_input[CONF_RELEASE_OFF])
            except ValueError:
                errors["base"] = "not_a_number"
            if errors:
                pass
            elif user_input[CONF_RELEASE_ON] == user_input[CONF_RELEASE_OFF]:
                errors["base"] = "same_release_values"
            else:
                self._data.update(user_input)
                if self.source == SOURCE_RECONFIGURE:
                    return await self._update()
                return self._create()
        entity_id = self._data[CONF_RELEASE_ENTITY]
        state = self.hass.states.get(entity_id)
        options = list(state.attributes.get("options", [])) if state else []
        if entity_id.split(".")[0] in NUMBER_DOMAINS:
            # e.g. RC310 summer threshold: 16 = heating allowed, 10 = blocked
            # (degrades gracefully: if HA dies, heating resumes once it gets cold)
            option_sel = selector.TextSelector()
        elif options:
            option_sel = selector.SelectSelector(selector.SelectSelectorConfig(options=options))
        else:
            option_sel = selector.TextSelector()
        schema = vol.Schema({vol.Required(CONF_RELEASE_ON): option_sel, vol.Required(CONF_RELEASE_OFF): option_sel})
        if self.source == SOURCE_RECONFIGURE:
            old = self._get_reconfigure_entry().data
            if old.get(CONF_RELEASE_ENTITY) == entity_id:  # same entity: keep its values as suggestion
                schema = self.add_suggested_values_to_schema(
                    schema, {k: old[k] for k in (CONF_RELEASE_ON, CONF_RELEASE_OFF) if k in old}
                )
        return self.async_show_form(step_id="release_values", data_schema=schema, errors=errors)

    def _create(self) -> ConfigFlowResult:
        title = self._data.pop(CONF_NAME, "Thermocast")
        return self.async_create_entry(title=title, data=self._data, options={})

    async def _update(self) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        title = self._data.pop(CONF_NAME, entry.title)
        old_release = {k: entry.data.get(k) for k in (CONF_RELEASE_ENTITY, CONF_RELEASE_ON, CONF_RELEASE_OFF)}
        new_release = {k: self._data.get(k) for k in old_release}
        coordinator = getattr(entry, "runtime_data", None)
        if old_release != new_release and coordinator is not None and coordinator.control_enabled:
            # fail-safe: never leave the old release entity blocked behind
            await coordinator.actuator.async_force_on()
        return self.async_update_and_abort(entry, title=title, data=self._data)  # update listener reloads

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ThermocastOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_ZONE: ZoneSubentryFlow}


class ThermocastOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        options = dict(self.config_entry.options)
        schema = self.add_suggested_values_to_schema(_options_schema(options), options)
        return self.async_show_form(step_id="init", data_schema=schema)


class ZoneSubentryFlow(ConfigSubentryFlow):
    """A zone = one room or a group of rooms sharing a heating circuit."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = self._clean(user_input)
            if not errors:
                return self.async_create_entry(title=data.pop(CONF_NAME), data=data)
        return self.async_show_form(step_id="user", data_schema=_zone_schema(), errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = self._clean(user_input)
            if not errors:
                title = data.pop(CONF_NAME)
                return self.async_update_and_abort(self._get_entry(), subentry, title=title, data=data)
        comfort = float(subentry.data[CONF_COMFORT_TEMP])
        defaults = {CONF_COMFORT_HIGH: comfort + DEFAULT_HIGH_OFFSET, CONF_BASE_TEMP: comfort - DEFAULT_BASE_OFFSET}
        schema = self.add_suggested_values_to_schema(
            _zone_schema(), {**defaults, **subentry.data, CONF_NAME: subentry.title}
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    @staticmethod
    def _clean(user_input: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        data = dict(user_input)
        errors: dict[str, str] = {}
        try:
            data[CONF_SURFACES] = _validate_surfaces(user_input.get(CONF_SURFACES))
        except (vol.Invalid, ValueError, KeyError, TypeError):
            errors[CONF_SURFACES] = "invalid_surfaces"
        if data.get(CONF_HEAT_TYPE) == "fbh" and data.get(CONF_VALVE_ENTITY):
            errors[CONF_VALVE_ENTITY] = "valve_only_radiator"
        if data.get(CONF_BT_CONTROL) and not data.get(CONF_BT_ENTITY):
            errors[CONF_BT_ENTITY] = "bt_entity_missing"
        comfort = float(data[CONF_COMFORT_TEMP])
        low = comfort - float(data[CONF_COMFORT_BAND])
        high = float(data.get(CONF_COMFORT_HIGH) or comfort + DEFAULT_HIGH_OFFSET)
        base = float(data.get(CONF_BASE_TEMP) or comfort - DEFAULT_BASE_OFFSET)
        if not base <= low or not high > comfort:
            errors["base"] = "invalid_bounds"
        if bool(data.get(CONF_QUIET_FROM)) != bool(data.get(CONF_QUIET_TO)):
            errors[CONF_QUIET_TO] = "quiet_incomplete"
        return data, errors
