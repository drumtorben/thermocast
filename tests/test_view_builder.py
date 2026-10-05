"""The coordinator builds the panel view next to (never inside) the control path."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .test_init import _setup_entry, _setup_states


async def test_view_without_recorder(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    view = mock_entry.runtime_data.view
    assert view["version"] == 1
    assert "no_recorder" in view["errors"]
    assert all(v is None for v in view["zones"][0]["measured"])
    now = view["window"]["now_index"]
    assert view["zones"][0]["plan"]["mean"][now] == 20.4
    assert view["candidates"]  # the future still works
    assert view["weather"]["irr"][0]["label"] == "East"


@pytest.mark.parametrize(("t_out", "temp"), [(-8.0, 20.3), (5.0, 20.5), (10.0, 20.25)])
async def test_observe_mode_plan_respects_min_block(
    hass: HomeAssistant, mock_entry, mock_open_meteo, t_out: float, temp: float
) -> None:
    """The shadow actuator gives the rollout a block history even though nothing is switched."""
    mock_open_meteo(t_out)
    await _setup_states(hass, temp=temp)
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    state = coordinator.view_builder._actuator_state(dt_util.utcnow(), coordinator.data)
    assert state.on is coordinator.data.want_heat
    if state.on:
        assert state.since_h < 1  # a block that starts now is young, not "infinitely old"
    view = coordinator.view
    end = view["window"]["end"]
    blocks = view["heating"]["planned_blocks"]
    assert blocks
    for b in blocks:
        if b["end"] != end:
            hours = (datetime.fromisoformat(b["end"]) - datetime.fromisoformat(b["start"])).total_seconds() / 3600
            assert hours >= 3, blocks


async def test_view_without_forecast(hass: HomeAssistant, mock_entry, aioclient_mock) -> None:
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    view = mock_entry.runtime_data.view
    assert "no_forecast" in view["errors"]
    assert view["explanation"]["code"] == "failsafe"
    assert view["candidates"] == []


async def test_view_error_does_not_touch_control(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    with patch("custom_components.thermocast.view_builder.build_view", side_effect=RuntimeError("boom")):
        await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    assert coordinator.view == {"error": "RuntimeError: boom"}
    assert coordinator.last_update_success is True
    assert coordinator.data.failsafe_reason is None


@pytest.mark.parametrize("expected_lingering_timers", [True])  # the schedule helper keeps its own timer
async def test_comfort_from_schedule(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    from homeassistant.setup import async_setup_component
    from homeassistant.util import dt as dt_util

    from custom_components.thermocast.const import CONF_COMFORT_SCHEDULE

    weekdays = {d: [{"from": "08:00:00", "to": "17:00:00"}] for d in ("monday", "tuesday", "wednesday",
                                                                       "thursday", "friday")}
    assert await async_setup_component(hass, "schedule", {"schedule": {"office": {"name": "Office", **weekdays}}})
    sub = next(iter(mock_entry.subentries.values()))
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_subentry(mock_entry, sub, data={**sub.data, CONF_COMFORT_SCHEDULE: "schedule.office"})
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)

    zone = mock_entry.runtime_data.view["zones"][0]
    hours = mock_entry.runtime_data.view["hours"]
    tz = dt_util.get_time_zone(hass.config.time_zone)
    for iso, low in zip(hours, zone["comfort_low"]):
        local = datetime.fromisoformat(iso).astimezone(tz)
        expected = local.weekday() < 5 and 8 <= local.hour < 17
        assert (low is not None) == expected, (local, low)
