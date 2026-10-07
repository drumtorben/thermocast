"""A (re)load must be quick: no waiting for the panel view, no second forecast fetch."""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from custom_components.thermocast.const import CONF_SURFACES

from .test_init import _setup_entry, _setup_states


async def test_setup_does_not_wait_for_the_panel_view(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    from custom_components.thermocast.view_builder import ViewBuilder

    await _setup_states(hass)
    gate = asyncio.Event()
    build = ViewBuilder._async_build

    async def slow_build(self, *args):
        await gate.wait()
        return await build(self, *args)

    with patch.object(ViewBuilder, "_async_build", slow_build):
        mock_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        assert mock_entry.state is ConfigEntryState.LOADED
        coordinator = mock_entry.runtime_data
        assert coordinator.data.failsafe_reason is None  # the control path is done
        assert coordinator.view is None  # the panel shows "loading" meanwhile

        pushed = []
        coordinator.view_builder.async_add_listener(lambda: pushed.append(coordinator.view))
        gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.view["version"] == 1
    assert pushed and pushed[-1]["version"] == 1


async def test_unload_during_a_view_build(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    from custom_components.thermocast.view_builder import ViewBuilder

    await _setup_states(hass)

    async def never(self, *args):
        await asyncio.Event().wait()

    with patch.object(ViewBuilder, "_async_build", never):
        mock_entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(mock_entry.entry_id)
        await hass.async_block_till_done()
        coordinator = mock_entry.runtime_data
        assert await hass.config_entries.async_unload(mock_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.view == {"error": "unloaded"}


async def test_reload_reuses_the_forecast(hass: HomeAssistant, mock_entry, mock_open_meteo, aioclient_mock) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    fetched = aioclient_mock.call_count
    assert fetched == 2  # temperature + one surface orientation
    first = mock_entry.runtime_data.forecast

    hass.config_entries.async_update_entry(mock_entry, options={**mock_entry.options, "starts_weight": 60})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_entry.runtime_data.config_entry.options["starts_weight"] == 60  # reloaded
    assert aioclient_mock.call_count == fetched
    assert mock_entry.runtime_data.forecast is first


async def test_new_surface_fetches_again(hass: HomeAssistant, mock_entry, mock_open_meteo, aioclient_mock) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    fetched = aioclient_mock.call_count
    sub = next(iter(mock_entry.subentries.values()))
    roof = {"kind": "roof", "azimuth": 180, "tilt": 40, "name": "South roof"}
    hass.config_entries.async_update_subentry(
        mock_entry, sub, data={**sub.data, CONF_SURFACES: [*sub.data[CONF_SURFACES], roof]}
    )
    await hass.async_block_till_done(wait_background_tasks=True)
    assert aioclient_mock.call_count == fetched + 3  # new forecast: temperature + both orientations
    assert set(mock_entry.runtime_data.forecast.irr) == {"0_0", "90_90", "40_180"}
