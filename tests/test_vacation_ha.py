"""Vacation inside Home Assistant: entities, store, planner, phase events, fail-safe."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.thermocast.core.vacation import Vacation

from .test_init import _eid, _setup_entry, _setup_states

NOW = datetime(2026, 10, 20, 10, 5, tzinfo=UTC)


def _ids(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    return {
        "start": _eid(hass, f"{entry_id}_vacation_start", "datetime"),
        "end": _eid(hass, f"{entry_id}_vacation_end", "datetime"),
        "floor": _eid(hass, f"{entry_id}_vacation_floor", "number"),
        "lead": _eid(hass, f"{entry_id}_vacation_lead", "number"),
        "active": _eid(hass, f"{entry_id}_vacation_active", "binary_sensor"),
    }


async def _set_period(hass: HomeAssistant, ids: dict[str, str], start: datetime, end: datetime) -> None:
    await hass.services.async_call("datetime", "set_value", {"entity_id": ids["start"], "datetime": start}, blocking=True)
    await hass.services.async_call("datetime", "set_value", {"entity_id": ids["end"], "datetime": end}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_entities_set_store_and_survive_a_reload(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer, hass_storage
) -> None:
    freezer.move_to(NOW)
    mock_open_meteo(-5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    ids = _ids(hass, mock_entry.entry_id)
    assert hass.states.get(ids["start"]).state == "unknown"
    assert hass.states.get(ids["floor"]).state == "15.0" and hass.states.get(ids["lead"]).state == "12.0"
    assert hass.states.get(ids["active"]).state == STATE_OFF

    await _set_period(hass, ids, NOW - timedelta(hours=1), NOW + timedelta(days=5))
    await hass.services.async_call("number", "set_value", {"entity_id": ids["floor"], "value": 14.5}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": ids["lead"], "value": 20}, blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    c = mock_entry.runtime_data
    assert c.vacation == Vacation(NOW - timedelta(hours=1), NOW + timedelta(days=5), floor=14.5, lead_h=20)
    assert all(z.vacation == c.vacation for z in c.zones.values())
    active = hass.states.get(ids["active"])
    assert active.state == STATE_ON and active.attributes["phase"] == "away"
    stored = hass_storage[f"thermocast.{mock_entry.entry_id}"]["data"]["vacation"]
    assert stored["floor"] == 14.5 and stored["lead_h"] == 20

    assert await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_entry.runtime_data.vacation == c.vacation
    assert hass.states.get(ids["floor"]).state == "14.5"


async def test_away_no_block_for_comfort(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """Cold outside, room below comfort: normally a block – away on vacation the room may cool to the floor."""
    freezer.move_to(NOW)
    mock_open_meteo(-5.0)
    await _setup_states(hass, temp=19.0)
    await _setup_entry(hass, mock_entry)
    c = mock_entry.runtime_data
    assert c.data.planner_heat is True
    await _set_period(hass, _ids(hass, mock_entry.entry_id), NOW - timedelta(hours=1), NOW + timedelta(days=7))
    await c.async_refresh()  # the second set_value fell into the refresh debounce
    zr = c.data.zones["zone_eg"]
    assert set(zr.comfort_low) == {15.0}
    assert c.data.planner_heat is False, zr.min_lower


async def test_phase_events_at_the_boundaries(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    freezer.move_to(NOW)
    mock_open_meteo(-5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    ids = _ids(hass, mock_entry.entry_id)
    start, end = NOW + timedelta(minutes=7), NOW + timedelta(hours=20)  # lead 12 h → preheat from +8 h
    await _set_period(hass, ids, start, end)
    c = mock_entry.runtime_data

    def types() -> list[str]:
        return [e["type"] for e in c.events.to_list() if e["type"].startswith("vacation_")]

    assert types() == []
    for at in (start, end - timedelta(hours=12), end):  # the boundary refresh, not the 15-min tick
        freezer.move_to(at + timedelta(seconds=1))
        async_fire_time_changed(hass, at + timedelta(seconds=1))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert types() == ["vacation_start", "vacation_preheat", "vacation_end"]
    assert hass.states.get(ids["active"]).state == STATE_OFF


async def test_failsafe_still_releases_on_vacation(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    freezer.move_to(NOW)
    mock_open_meteo(-5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    await _set_period(hass, _ids(hass, mock_entry.entry_id), NOW - timedelta(hours=1), NOW + timedelta(days=7))
    hass.states.async_set("sensor.living", "unavailable")
    hass.states.async_set("sensor.kitchen", "unavailable")
    freezer.tick(timedelta(minutes=10))  # past the startup grace
    await mock_entry.runtime_data.async_refresh()
    release = hass.states.get(_eid(hass, f"{mock_entry.entry_id}_heating_release", "binary_sensor"))
    assert release.state == STATE_ON and release.attributes["failsafe_reason"] == "no_temperature:EG"
