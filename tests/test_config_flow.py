"""Config flow (house), options flow and zone subentry flow."""
from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermocast.const import (
    CONF_BURNER_STARTS,
    CONF_CALIBRATE_SIGMA,
    CONF_CONFIDENCE_Z,
    CONF_DHW_ENTITY,
    CONF_FLOW_TEMP_SENSOR,
    CONF_FORGETTING,
    CONF_HEAT_ENERGY,
    CONF_HEAT_TYPE,
    CONF_MAX_SWITCHES,
    CONF_MIN_BLOCK_H,
    CONF_MIN_PAUSE_H,
    CONF_OUTDOOR_SENSOR,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    CONF_SURFACES,
    CONF_VALVE_ENTITY,
    DOMAIN,
    SUBENTRY_ZONE,
)

from .conftest import ZONE_DATA

HOUSE_INPUT = {
    CONF_NAME: "Haus",
    CONF_OUTDOOR_SENSOR: "sensor.outdoor",
    CONF_FLOW_TEMP_SENSOR: "sensor.flow",
}


async def _start(hass: HomeAssistant, release_entity: str):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOUSE_INPUT, CONF_RELEASE_ENTITY: release_entity}
    )


async def test_user_flow_switch(hass: HomeAssistant) -> None:
    result = await _start(hass, "input_boolean.heating_allowed")
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Haus"
    assert result["data"][CONF_RELEASE_ENTITY] == "input_boolean.heating_allowed"
    assert CONF_NAME not in result["data"]


async def test_user_flow_number_values(hass: HomeAssistant) -> None:
    result = await _start(hass, "number.summer_threshold")
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "release_values"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RELEASE_ON: "abc", CONF_RELEASE_OFF: "10"}
    )
    assert result["errors"] == {"base": "not_a_number"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RELEASE_ON: "16", CONF_RELEASE_OFF: "16"}
    )
    assert result["errors"] == {"base": "same_release_values"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RELEASE_ON: "16", CONF_RELEASE_OFF: "10"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_RELEASE_ON] == "16"
    assert result["data"][CONF_RELEASE_OFF] == "10"


async def test_user_flow_select_offers_options(hass: HomeAssistant) -> None:
    hass.states.async_set("select.season", "winter", {"options": ["summer", "winter", "auto"]})
    result = await _start(hass, "select.season")
    assert result["step_id"] == "release_values"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RELEASE_ON: "winter", CONF_RELEASE_OFF: "summer"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_single_instance(hass: HomeAssistant, mock_entry: MockConfigEntry) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, release_entity: str):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    return result, await hass.config_entries.flow.async_configure(
        result["flow_id"], {**HOUSE_INPUT, CONF_RELEASE_ENTITY: release_entity}
    )


async def test_reconfigure_select_to_threshold(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo
) -> None:
    """Wrong release entity picked at setup (summer/winter select): switch to the threshold, keep the zones."""
    hass.states.async_set("select.season", "winter", {"options": ["summer", "winter"]})
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry,
        data={**mock_entry.data, CONF_RELEASE_ENTITY: "select.season", CONF_RELEASE_ON: "winter",
              CONF_RELEASE_OFF: "summer"},
    )
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    form, result = await _reconfigure(hass, mock_entry, "number.summer_threshold")
    keys = {str(k): k for k in form["data_schema"].schema}
    assert keys[CONF_RELEASE_ENTITY].description == {"suggested_value": "select.season"}
    assert result["step_id"] == "release_values"
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_RELEASE_ON].description is None  # other entity: old select options are no suggestion

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_RELEASE_ON: "16", CONF_RELEASE_OFF: "10"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    assert mock_entry.title == "Haus"
    assert mock_entry.data[CONF_RELEASE_ENTITY] == "number.summer_threshold"
    assert (mock_entry.data[CONF_RELEASE_ON], mock_entry.data[CONF_RELEASE_OFF]) == ("16", "10")
    assert list(mock_entry.subentries) == ["zone_eg"]
    assert mock_entry.runtime_data.actuator.entity_id == "number.summer_threshold"


async def test_reconfigure_suggests_values_of_same_entity(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo
) -> None:
    mock_entry.add_to_hass(hass)
    _, result = await _reconfigure(hass, mock_entry, mock_entry.data[CONF_RELEASE_ENTITY])
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_RELEASE_ON].description == {"suggested_value": "16"}
    assert keys[CONF_RELEASE_OFF].description == {"suggested_value": "10"}


async def test_reconfigure_releases_old_entity_when_controlling(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo
) -> None:
    """Fail-safe: with control on, the old release entity is set to 'heating allowed' before the switch."""
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = mock_entry.runtime_data
    coordinator.control_enabled = True
    released: list[str] = []

    async def force_on(*_args) -> None:
        released.append(coordinator.actuator.entity_id)

    with patch.object(coordinator.actuator, "async_force_on", force_on):
        _, result = await _reconfigure(hass, mock_entry, "input_boolean.heating_allowed")
        assert result["type"] is FlowResultType.ABORT
        await hass.async_block_till_done()
    # first the old entity, then (unload of the reload) the new one
    assert released[0] == "input_number.summer_threshold"
    assert CONF_RELEASE_ON not in mock_entry.data  # switch-type entities need no values


async def test_options_flow(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    options = {
        CONF_MIN_BLOCK_H: 4,
        CONF_MIN_PAUSE_H: 2,
        CONF_MAX_SWITCHES: 8,
        CONF_FORGETTING: 0.995,
        CONF_CONFIDENCE_Z: 1.5,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], options)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_entry.options == {**options, CONF_CALIBRATE_SIGMA: True}


async def test_options_flow_panel_sources(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    mock_entry.add_to_hass(hass)
    base = {CONF_MIN_BLOCK_H: 3, CONF_MIN_PAUSE_H: 2, CONF_MAX_SWITCHES: 12, CONF_FORGETTING: 0.996,
            CONF_CONFIDENCE_Z: 1.0}
    sources = {
        CONF_DHW_ENTITY: "binary_sensor.dhw_active",
        CONF_BURNER_STARTS: "sensor.burner_starts",
        CONF_HEAT_ENERGY: "sensor.energy_heating",
    }
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {**base, **sources})
    assert mock_entry.options == {**base, **sources, CONF_CALIBRATE_SIGMA: True}

    # the form suggests the stored entities and they can be removed again
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_BURNER_STARTS].description == {"suggested_value": "sensor.burner_starts"}
    result = await hass.config_entries.options.async_configure(result["flow_id"], base)
    assert mock_entry.options == {**base, CONF_CALIBRATE_SIGMA: True}


async def test_zone_subentry_create(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    bad = {**ZONE_DATA, CONF_NAME: "Bad", CONF_SURFACES: [{"kind": "door", "azimuth": 90}]}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["errors"] == {CONF_SURFACES: "invalid_surfaces"}

    bad = {**ZONE_DATA, CONF_NAME: "Bad", CONF_VALVE_ENTITY: "sensor.valve"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["errors"] == {CONF_VALVE_ENTITY: "valve_only_radiator"}

    zone = {
        **ZONE_DATA,
        CONF_NAME: "Eltern",
        CONF_HEAT_TYPE: "radiator",
        CONF_VALVE_ENTITY: "sensor.valve",
        CONF_SURFACES: [{"kind": "window", "azimuth": 90, "tilt": 90}, {"kind": "roof", "azimuth": 180, "tilt": 40}],
    }
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], zone)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in mock_entry.subentries.values() if s.title == "Eltern")
    assert sub.data[CONF_HEAT_TYPE] == "radiator"
    assert [s["kind"] for s in sub.data[CONF_SURFACES]] == ["window", "roof"]


async def test_zone_added_during_setup_is_loaded(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo
) -> None:
    """Two zones saved in quick succession: the second arrives while the reload of the first runs."""
    from homeassistant.config_entries import ConfigSubentry

    from custom_components.thermocast.coordinator import ThermocastCoordinator

    original = ThermocastCoordinator.async_load
    added = False

    async def load_and_add_zone(self):
        nonlocal added
        await original(self)
        if not added:
            added = True
            hass.config_entries.async_add_subentry(
                mock_entry,
                ConfigSubentry(data={**ZONE_DATA}, subentry_type=SUBENTRY_ZONE, title="Late", unique_id=None),
            )

    mock_entry.add_to_hass(hass)
    with patch.object(ThermocastCoordinator, "async_load", load_and_add_zone):
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    titles = sorted(z.title for z in mock_entry.runtime_data.zones.values())
    assert titles == ["EG", "Late"]


async def test_zone_subentry_reconfigure(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_RECONFIGURE, "subentry_id": "zone_eg"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {**ZONE_DATA, CONF_NAME: "Erdgeschoss", CONF_SURFACES: []}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()

    sub = mock_entry.subentries["zone_eg"]
    assert sub.title == "Erdgeschoss"
    assert sub.data[CONF_SURFACES] == []
    # update listener reloaded the entry with the new zone layout
    assert mock_entry.runtime_data.zones["zone_eg"].model.spec.surfaces == ()
