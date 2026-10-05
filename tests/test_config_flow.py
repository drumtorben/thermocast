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
