"""Better Thermostat control inside Home Assistant: writes, manual override, observe mode, fail-safe."""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed, async_mock_service

from custom_components.thermocast.const import (
    CONF_BASE_TEMP,
    CONF_BT_CONTROL,
    CONF_BT_ENTITY,
    CONF_COMFORT_HIGH,
    CONF_HEAT_TYPE,
)

from .test_init import _setup_states

BT = "climate.bt_office"


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, freezer, target: float = 19.0, temp: float = 20.4):
    freezer.move_to("2026-10-04 10:05:00+00:00")  # 12:05 local, comfort time
    await _setup_states(hass, temp=temp)
    hass.states.async_set(BT, "heat", {"temperature": target, "current_temperature": temp})
    calls = async_mock_service(hass, "climate", "set_temperature")
    entry.add_to_hass(hass)
    hass.config_entries.async_update_subentry(
        entry, entry.subentries["zone_eg"],
        data={**entry.subentries["zone_eg"].data, CONF_HEAT_TYPE: "radiator", CONF_BT_CONTROL: True,
              CONF_BT_ENTITY: BT, CONF_COMFORT_HIGH: 21.5, CONF_BASE_TEMP: 18.0},
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return calls


async def _tick(hass: HomeAssistant, freezer, minutes: int = 15) -> None:
    from datetime import timedelta

    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_observe_mode_shows_but_never_writes(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    calls = await _setup(hass, mock_entry, freezer)
    bt = mock_entry.runtime_data.data.bt["zone_eg"]
    assert calls == []
    assert bt["reason"] in ("observe", "unchanged") and bt["target"] in (20.0, 21.5)
    hass.states.async_set(BT, "heat", {"temperature": 23.0})  # someone turns the knob – not our business yet
    await _tick(hass, freezer)
    assert mock_entry.runtime_data.data.bt["zone_eg"]["reason"] != "override" and calls == []


async def test_control_writes_once_and_detects_a_manual_change(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    calls = await _setup(hass, mock_entry, freezer)
    coordinator = mock_entry.runtime_data
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(calls) == 1
    written = calls[0].data["temperature"]
    assert written in (20.0, 21.5)  # lower bound (20.5 − 0.3 → 20.0) or charge target
    hass.states.async_set(BT, "heat", {"temperature": written, "current_temperature": 20.4})

    await _tick(hass, freezer)
    assert len(calls) == 1  # unchanged: nothing written again

    hass.states.async_set(BT, "heat", {"temperature": 23.0, "current_temperature": 20.4})  # by hand
    await _tick(hass, freezer)
    bt = coordinator.data.bt["zone_eg"]
    assert bt["reason"] == "override" and bt["override_until"]
    assert len(calls) == 1
    assert "bt_override" in [e["type"] for e in coordinator.events.to_list()]

    await _tick(hass, freezer, minutes=4 * 60)  # override over: Thermocast takes the room back
    assert len(calls) == 2


async def test_changes_made_in_observe_mode_are_no_override(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    calls = await _setup(hass, mock_entry, freezer)
    coordinator = mock_entry.runtime_data
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    await coordinator.async_set_control(False)  # writes the lower bound
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(BT, "heat", {"temperature": 22.5})  # adjusted by hand while observing
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.bt["zone_eg"]["reason"] != "override"
    assert "bt_override" not in [e["type"] for e in coordinator.events.to_list()]
    assert calls[-1].data["temperature"] in (20.0, 21.5)  # Thermocast takes the room over again


async def test_update_error_holds_the_lower_bound(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    from unittest.mock import patch

    calls = await _setup(hass, mock_entry, freezer)
    coordinator = mock_entry.runtime_data
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    coordinator.zone_actuator.state["zone_eg"].last_written = 21.5  # as if a block were charging
    hass.states.async_set(BT, "heat", {"temperature": 21.5})
    n = len(calls)
    with patch.object(coordinator, "_forecast_and_plan", side_effect=RuntimeError("boom")):
        await _tick(hass, freezer)
    assert len(calls) == n + 1 and calls[-1].data["temperature"] == 20.0


async def test_thermostat_late_after_start_is_picked_up_within_a_minute(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    """Better Thermostat loads after Thermocast on an HA start – check again soon, not after 15 min."""
    calls = await _setup(hass, mock_entry, freezer)
    hass.states.async_set(BT, "unavailable", {})
    coordinator = mock_entry.runtime_data
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert coordinator.data.bt["zone_eg"]["reason"] == "unavailable"
    hass.states.async_set(BT, "heat", {"temperature": 19.0})
    await _tick(hass, freezer, minutes=1)
    assert coordinator.data.bt["zone_eg"]["reason"] != "unavailable" and calls


async def test_unavailable_thermostat_is_left_alone(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    calls = await _setup(hass, mock_entry, freezer)
    hass.states.async_set(BT, "unavailable", {})
    await mock_entry.runtime_data.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls == [] and mock_entry.runtime_data.data.bt["zone_eg"]["reason"] == "unavailable"


async def test_control_off_holds_the_lower_bound(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    calls = await _setup(hass, mock_entry, freezer, target=21.5)
    coordinator = mock_entry.runtime_data
    await coordinator.async_set_control(True)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.states.async_set(BT, "heat", {"temperature": calls[-1].data["temperature"] if calls else 21.5})
    n = len(calls)
    await coordinator.async_set_control(False)
    await hass.async_block_till_done(wait_background_tasks=True)
    floor_writes = [c for c in calls[n:] if c.data["temperature"] == 20.0]
    assert floor_writes or (calls and calls[-1].data["temperature"] == 20.0)
    assert coordinator.zone_actuator.to_dict()["zone_eg"]["last_written"] == 20.0
