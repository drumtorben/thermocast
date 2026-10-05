"""Event log: ring buffer, dedupe, persistence and coordinator hooks."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.thermocast.const import DOMAIN
from custom_components.thermocast.events import EventLog

from .test_init import _setup_entry, _setup_states

T = datetime(2026, 10, 4, 12, tzinfo=UTC)


def test_ring_buffer_and_dedupe():
    log = EventLog(maxlen=3)
    for i in range(5):
        log.add(T + timedelta(minutes=i), "control_on")
    assert len(log.to_list()) == 3
    assert log.add(T, "forecast_failed") is True
    assert log.add(T + timedelta(minutes=15), "forecast_failed", dedupe=timedelta(hours=1)) is False
    assert log.add(T + timedelta(hours=2), "forecast_failed", dedupe=timedelta(hours=1)) is True
    restored = EventLog(maxlen=3)
    restored.load(log.to_list())
    assert restored.to_list() == log.to_list() and restored.dirty is False


async def test_coordinator_records_events(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_storage) -> None:
    mock_open_meteo(18.0)
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{mock_entry.entry_id}_control_enabled")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    types = [e["type"] for e in mock_entry.runtime_data.events.to_list()]
    assert types == ["control_on", "release_written", "control_off", "release_written"]
    stored = hass_storage[f"{DOMAIN}.{mock_entry.entry_id}"]["data"]
    assert [e["type"] for e in stored["events"]] == types


async def test_failsafe_and_forecast_events(hass: HomeAssistant, mock_entry, aioclient_mock) -> None:
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    types = [e["type"] for e in mock_entry.runtime_data.events.to_list()]
    assert "forecast_failed" in types and "failsafe_start" in types


async def test_failsafe_end_after_reload(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    hass.states.async_set("sensor.living", "unavailable")
    hass.states.async_set("sensor.kitchen", "unavailable")
    await _setup_entry(hass, mock_entry)
    assert mock_entry.runtime_data.events.to_list()[-1]["type"] == "failsafe_start"

    hass.states.async_set("sensor.living", "20.4")
    assert await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_entry.runtime_data.events.to_list()[-1]["type"] == "failsafe_end"
