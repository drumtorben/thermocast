"""A (re)load must be quick: no waiting for the panel view, no second forecast fetch."""
from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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


async def test_update_during_a_build_is_built_afterwards(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    """Regression: a warm start finished while the view of the old models was being built – the panel kept the
    old plan until the next hour. Every update now gets its own build (the latest one, after the running one)."""
    from custom_components.thermocast.view_builder import ViewBuilder

    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    gate = asyncio.Event()
    build = ViewBuilder._async_build
    q_on_seen: list[float] = []

    async def slow_build(self, *args):
        q_on_seen.append(coordinator.zones["zone_eg"].q_on)
        await gate.wait()
        return await build(self, *args)

    with patch.object(ViewBuilder, "_async_build", slow_build):
        await coordinator.async_refresh()  # build 1 starts (old model) and waits
        await hass.async_block_till_done()
        coordinator.zones["zone_eg"].q_on = 15.0  # e.g. a warm start replaced the models
        await coordinator.async_refresh()  # arrives during build 1
        await coordinator.async_refresh()  # … and another one: only the latest is built
        gate.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert len(q_on_seen) == 2 and q_on_seen[-1] == 15.0


async def test_forecast_log_keeps_the_first_plan_of_the_hour(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    """Rebuilding every 15 min must not turn the logged '1 h ahead' forecast into a 15-min one."""
    freezer.move_to("2026-10-08 10:01:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    flog = coordinator.zones["zone_eg"].flog
    first = dict(flog.to_dict())
    assert "2026-10-08T10:00:00+00:00" in first
    hass.states.async_set("sensor.living", "19.0", {"device_class": "temperature"})  # a different plan at :16
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert flog.to_dict()["2026-10-08T10:00:00+00:00"] == first["2026-10-08T10:00:00+00:00"]


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
