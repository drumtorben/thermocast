"""A frozen temperature sensor: silent while the model expects the room to change -> not learned, does not
lead, and once no leading zone measures any more the fail-safe allows heating. It ends with the next report."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from .test_init import _setup_entry, _setup_states


async def _tick(hass: HomeAssistant, freezer, minutes: int) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_frozen_sensor_is_not_learned_and_does_not_lead(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    from custom_components.thermocast.coordinator import build_plan_inputs

    freezer.move_to("2026-11-08 19:30:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass, temp=19.8)
    await _setup_entry(hass, mock_entry)
    c = mock_entry.runtime_data
    z = next(iter(c.zones.values()))
    z.model.n_updates = 500  # a trained model: it may judge the silence

    with patch("custom_components.thermocast.coordinator.expected_change", return_value=0.35):
        for _ in range(8):  # 19:30 -> 21:30, the sensors never report: 0.175 K after hour 19, 0.525 after 20
            await _tick(hass, freezer, 15)
        assert z.stale_since is None and abs(z.silent_drift - 0.525) < 1e-9
        for _ in range(4):  # 22:00: hour 21 closes, 0.875 K expected and still nothing
            await _tick(hass, freezer, 15)

    assert z.stale_since is not None
    ev = [e for e in c.events.to_list() if e["type"] == "sensor_stale"]
    assert len(ev) == 1 and ev[0]["zone"] == z.subentry_id and ev[0]["detail"] == "+0.9 K"
    log = z.log.to_list()
    assert log[-1]["t"].startswith("2026-11-08T21:00") and log[-1]["rec"]["valid"] is False
    fc = c.forecast
    zi = build_plan_inputs(hass, c.zones, fc, fc.index_of(dt_util.utcnow()), 6)[0]
    assert not zi.leads_release
    assert c.data.failsafe_reason == "stale_temperature"  # the only leading zone: heating allowed

    hass.states.async_set("sensor.living", "21.4", {"device_class": "temperature"})  # it reports again
    await _tick(hass, freezer, 15)
    assert z.stale_since is None and z.silent_drift == 0.0
    assert [e["type"] for e in c.events.to_list()][-2:] == ["sensor_back", "failsafe_end"]
    assert c.data.failsafe_reason is None


async def test_unchanged_report_is_a_report(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """A sensor that reports periodically with the same value (``last_reported``) is not frozen."""
    freezer.move_to("2026-11-08 19:30:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass, temp=19.8)
    await _setup_entry(hass, mock_entry)
    z = next(iter(mock_entry.runtime_data.zones.values()))
    z.model.n_updates = 500

    with patch("custom_components.thermocast.coordinator.expected_change", return_value=0.35):
        for _ in range(16):
            hass.states.async_set("sensor.kitchen", "19.8", {"device_class": "temperature"})
            await _tick(hass, freezer, 15)
    assert z.stale_since is None and z.silent_drift < 0.35


async def test_untrained_model_cannot_call_a_sensor_frozen(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    freezer.move_to("2026-11-08 19:30:00+00:00")
    mock_open_meteo(-5.0)
    await _setup_states(hass, temp=19.8)
    await _setup_entry(hass, mock_entry)
    z = next(iter(mock_entry.runtime_data.zones.values()))
    for _ in range(16):  # 4 h silent at -5 °C: the prior would expect a lot of cooling
        await _tick(hass, freezer, 15)
    assert z.stale_since is None and z.silent_drift == 0.0
