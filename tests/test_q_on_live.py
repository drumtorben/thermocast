"""q_on in the live loop: only hours in which the heating pump ran (almost) the whole hour count."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.thermocast.core.model import Q_ON_ALPHA

from .test_init import _setup_entry, _setup_states


async def _hour(hass: HomeAssistant, freezer, pump: list[str]) -> None:
    """One hour of 15-min updates with the pump state per update."""
    for state in pump:
        hass.states.async_set("binary_sensor.heating_pump", state)
        freezer.tick(timedelta(minutes=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)


async def test_q_on_learns_from_full_pump_hours_only(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    freezer.move_to("2026-10-08 05:59:00+00:00")
    mock_open_meteo(10.0)
    await _setup_states(hass, temp=20.0)  # flow 35 °C → q = 15 while the pump runs
    await _setup_entry(hass, mock_entry)
    z = mock_entry.runtime_data.zones["zone_eg"]
    z.q_on = 3.0  # learned from the old always-on, low-flow operation

    await _hour(hass, freezer, ["on", "on", "on", "on"])  # 06:14 … 06:59: a full block hour
    await _hour(hass, freezer, ["on"])  # 07:14 closes hour 06
    assert z.q_on == pytest.approx((1 - Q_ON_ALPHA) * 3.0 + Q_ON_ALPHA * 15.0, abs=0.01)

    learned = z.q_on
    await _hour(hass, freezer, ["off", "off", "off"])  # hour 07: pump only at the first sample (overrun)
    await _hour(hass, freezer, ["off"])  # 08:14 closes hour 07
    assert z.q_on == pytest.approx(learned)
