"""Panel view with a real recorder (needs the recorder fixture before `hass`)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .test_init import _setup_entry, _setup_states


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations: None) -> None:
    """Override the conftest fixture: the recorder must be set up before hass."""


async def test_view_built_with_recorder(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    freezer.move_to("2026-10-04 08:30:00+00:00")
    await _setup_states(hass)
    hass.states.async_set("binary_sensor.heating_pump", "on")
    await async_wait_recording_done(hass)
    freezer.tick(timedelta(minutes=30))
    hass.states.async_set("sensor.living", "21.4", {"device_class": "temperature"})
    hass.states.async_set("binary_sensor.heating_pump", "off")
    await async_wait_recording_done(hass)
    freezer.tick(timedelta(minutes=35))  # 09:35
    mock_open_meteo(-5.0)
    await _setup_entry(hass, mock_entry)

    view = mock_entry.runtime_data.view
    assert view["version"] == 1, view
    assert view["errors"] == []
    hours = view["hours"]
    i8 = hours.index("2026-10-04T08:00:00+00:00")
    zone = view["zones"][0]
    assert zone["id"] == "zone_eg" and zone["name"] == "EG"
    # 08:30–09:00: living 20.4 / kitchen 20.4 -> 20.4
    assert zone["measured"][i8] == 20.4
    # 09:00–09:35: living 21.4, kitchen 20.4 -> 20.9
    assert zone["measured"][i8 + 1] == 20.9
    assert view["heating"]["actual"][i8] == 1.0  # pump on 08:30–09:00 (state at window start included)
    assert view["heating"]["actual"][i8 + 1] == 0.0
    assert view["decision"]["override"] == "observe"
    assert view["decision"]["rules"]["max_switches"] == 12
    assert view["candidates"]
    assert view["weather"]["irr"][0]["label"] == "East"


async def test_view_burner_time_beside_pump_time(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """The pump runs through the boiler's anti-cycling pause; the burner entity tells the burner minutes."""
    from custom_components.thermocast.const import CONF_BURNER_ACTIVE

    freezer.move_to("2026-10-04 08:00:00+00:00")
    await _setup_states(hass)
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("binary_sensor.burner_gas", "on")
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 08:15:00+00:00")
    hass.states.async_set("binary_sensor.burner_gas", "off")  # anti-cycling pause, pump keeps running
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 08:45:00+00:00")
    hass.states.async_set("binary_sensor.burner_gas", "on")
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 09:35:00+00:00")
    mock_open_meteo(-5.0)
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_BURNER_ACTIVE: "binary_sensor.burner_gas"})
    await _setup_entry(hass, mock_entry)

    view = mock_entry.runtime_data.view
    i8 = view["hours"].index("2026-10-04T08:00:00+00:00")
    assert view["heating"]["actual"][i8] == 1.0
    assert view["heating"]["burner"][i8] == pytest.approx(0.5)


async def test_view_frost_protection_pump_is_no_heating(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    freezer.move_to("2026-10-04 08:30:00+00:00")
    await _setup_states(hass)
    hass.states.async_set("input_number.summer_threshold", "10.0")
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("sensor.flow", "23.5", {"device_class": "temperature"})
    await async_wait_recording_done(hass)
    freezer.tick(timedelta(minutes=65))  # 09:35
    mock_open_meteo(-5.0)
    await _setup_entry(hass, mock_entry)

    view = mock_entry.runtime_data.view
    i8 = view["hours"].index("2026-10-04T08:00:00+00:00")
    assert view["heating"]["actual"][i8] == 0.0  # the pump ran, nothing heated
    assert view["days"][1]["heat_hours"] == 0.0


async def test_view_hot_water_charge_does_not_hide_a_frost_protection_hour(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    """A 15-min charge at 75 °C lifts the hour's mean flow above the limit; the circulation still heats nothing."""
    from custom_components.thermocast.const import CONF_DHW_ENTITY

    freezer.move_to("2026-10-04 08:00:00+00:00")
    await _setup_states(hass)
    hass.states.async_set("input_number.summer_threshold", "10.0")
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("binary_sensor.dhw_charging", "off")
    hass.states.async_set("sensor.flow", "23.5", {"device_class": "temperature"})
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 08:30:00+00:00")
    hass.states.async_set("binary_sensor.dhw_charging", "on")
    hass.states.async_set("sensor.flow", "75.0", {"device_class": "temperature"})
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 08:45:00+00:00")
    hass.states.async_set("binary_sensor.dhw_charging", "off")
    hass.states.async_set("sensor.flow", "23.5", {"device_class": "temperature"})
    await async_wait_recording_done(hass)
    freezer.move_to("2026-10-04 09:35:00+00:00")
    mock_open_meteo(-5.0)
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_DHW_ENTITY: "binary_sensor.dhw_charging"})
    await _setup_entry(hass, mock_entry)

    view = mock_entry.runtime_data.view
    i8 = view["hours"].index("2026-10-04T08:00:00+00:00")
    assert view["heating"]["actual"][i8] == 0.0


async def test_warm_start_learns_burner_cycling(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """Setup with a starts counter and no learned rate: the recorder's block hours teach it."""
    from custom_components.thermocast.const import CONF_BURNER_STARTS
    from custom_components.thermocast.warmstart import CYCLE_MIN_SAMPLES

    freezer.move_to("2026-10-04 00:00:00+00:00")
    await _setup_states(hass)
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("sensor.flow", "45.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.burner_starts", "100")
    await async_wait_recording_done(hass)
    hours = CYCLE_MIN_SAMPLES + 4
    for h in range(hours):  # one restart per block hour
        freezer.move_to(f"2026-10-04 {h:02d}:30:00+00:00")
        hass.states.async_set("sensor.burner_starts", str(101 + h))
        await async_wait_recording_done(hass)
    freezer.move_to(f"2026-10-04 {hours:02d}:10:00+00:00")
    mock_open_meteo(-5.0)
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_BURNER_STARTS: "sensor.burner_starts"})
    await _setup_entry(hass, mock_entry)

    c = mock_entry.runtime_data
    assert c.cycle.n >= CYCLE_MIN_SAMPLES
    r0, _ = c.cycle.rates(0)
    assert r0 > 0.3  # the floor heating alone restarts – the prior said 0
