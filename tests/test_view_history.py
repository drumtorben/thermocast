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
