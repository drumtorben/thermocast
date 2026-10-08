"""Airing: window changes are followed by state change, the plan starts from the temperature before airing
for an hour after closing, and neither the airing nor the recovery is learned. Plus: a zone model on its
prior (new or reset) holds the release until the warm start."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.thermocast.const import CONF_WINDOW_ENTITIES, DOMAIN, SUBENTRY_ZONE

from .conftest import HOUSE_DATA, ZONE_DATA
from .test_init import _setup_entry, _setup_states


@pytest.fixture
def window_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN, title="Thermocast", data=dict(HOUSE_DATA), options={},
        subentries_data=[{
            "data": {**ZONE_DATA, CONF_WINDOW_ENTITIES: ["binary_sensor.window"]},
            "subentry_id": "zone_eg", "subentry_type": SUBENTRY_ZONE, "title": "EG", "unique_id": None,
        }],
    )


def _temps(hass: HomeAssistant, temp: float) -> None:
    hass.states.async_set("sensor.living", str(temp), {"device_class": "temperature"})
    hass.states.async_set("sensor.kitchen", str(temp), {"device_class": "temperature"})


async def _tick(hass: HomeAssistant, freezer, minutes: int) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_short_airing_between_updates(hass: HomeAssistant, window_entry, mock_open_meteo, freezer) -> None:
    from custom_components.thermocast.coordinator import build_plan_inputs

    freezer.move_to("2026-10-08 05:30:00+00:00")
    mock_open_meteo(10.0)
    await _setup_states(hass, temp=21.0)
    hass.states.async_set("binary_sensor.window", "off")
    await _setup_entry(hass, window_entry)
    c = window_entry.runtime_data
    z = c.zones["zone_eg"]

    # 10 min airing between two updates (05:30 and 05:45): the air drops by 1 K
    freezer.tick(timedelta(minutes=2))
    hass.states.async_set("binary_sensor.window", "on")
    await hass.async_block_till_done()
    assert z.window_before == 21.0 and z.window_seen
    freezer.tick(timedelta(minutes=10))
    _temps(hass, 20.0)
    hass.states.async_set("binary_sensor.window", "off")
    await hass.async_block_till_done()
    ev = c.events.to_list()[-1]
    assert ev["type"] == "window_recovery" and ev["zone"] == "zone_eg" and ev["detail"] == "21.0 °C"

    # the plan starts from the temperature before airing – the zone still leads
    fc = c.forecast
    zi = build_plan_inputs(hass, c.zones, fc, fc.index_of(_now()), 6)[0]
    assert zi.temp_now == 21.0 and zi.leads_release

    await _tick(hass, freezer, 3)  # 05:45 update: the airing is in this hour's samples
    assert z.acc["window"][-1] is True and not z.window_seen
    await _tick(hass, freezer, 15)  # 06:00: hour 05 closes – not learned
    await _tick(hass, freezer, 15)
    log = z.log.to_list()
    assert log[-1]["t"].startswith("2026-10-08T05:00") and log[-1]["rec"]["valid"] is False

    for _ in range(4):  # 07:00: hour 06 (the air recovering until 06:42) is not learned either
        await _tick(hass, freezer, 15)
    log = z.log.to_list()
    assert log[-1]["t"].startswith("2026-10-08T06:00") and log[-1]["rec"]["valid"] is False

    # after the recovery hour the measurement counts again
    zi = build_plan_inputs(hass, c.zones, c.forecast, c.forecast.index_of(_now()), 6)[0]
    assert zi.temp_now == 20.0


async def test_recovery_survives_a_reload(hass: HomeAssistant, window_entry, mock_open_meteo, freezer) -> None:
    from custom_components.thermocast.coordinator import build_plan_inputs

    freezer.move_to("2026-10-08 05:30:00+00:00")
    mock_open_meteo(10.0)
    await _setup_states(hass, temp=21.0)
    hass.states.async_set("binary_sensor.window", "off")
    await _setup_entry(hass, window_entry)
    hass.states.async_set("binary_sensor.window", "on")
    await hass.async_block_till_done()
    _temps(hass, 20.0)
    hass.states.async_set("binary_sensor.window", "off")
    await hass.async_block_till_done()

    assert await hass.config_entries.async_reload(window_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    c = window_entry.runtime_data
    assert c.zones["zone_eg"].window_before == 21.0
    zi = build_plan_inputs(hass, c.zones, c.forecast, c.forecast.index_of(_now()), 6)[0]
    assert zi.temp_now == 21.0


async def test_open_window_at_startup_is_no_airing_event(
    hass: HomeAssistant, window_entry, mock_open_meteo
) -> None:
    await _setup_states(hass, temp=21.0)
    hass.states.async_set("binary_sensor.window", "on")
    await _setup_entry(hass, window_entry)
    z = window_entry.runtime_data.zones["zone_eg"]
    assert z.window_open and z.window_before is None
    hass.states.async_set("binary_sensor.window", "off")
    await hass.async_block_till_done()
    # closed without a known temperature from before: plan from the measurement
    ev = window_entry.runtime_data.events.to_list()[-1]
    assert z.window_closed is not None and ev["type"] == "window_recovery" and "detail" not in ev


async def test_prior_model_holds_the_release_until_the_warm_start(
    hass: HomeAssistant, mock_entry, mock_open_meteo, hass_storage
) -> None:
    """A zone change resets its model: the prior's plan must not switch the boiler before the warm start."""
    from custom_components.thermocast.coordinator import STORAGE_VERSION

    hass_storage[f"{DOMAIN}.{mock_entry.entry_id}"] = {
        "version": STORAGE_VERSION, "key": f"{DOMAIN}.{mock_entry.entry_id}",
        "data": {"control_enabled": True, "actuator": {"commanded": False}, "zones": {}, "events": []},
    }
    await _setup_states(hass, temp=19.0, release=10)  # cold: the planner wants heat
    seen: dict[str, object] = {}

    async def fake_warmstart(coordinator, only_fresh: bool) -> dict[str, int]:
        seen["override"] = coordinator.data.override
        seen["release"] = hass.states.get("input_number.summer_threshold").state
        return {}

    with patch("custom_components.thermocast.warmstart.async_warmstart", fake_warmstart):
        await _setup_entry(hass, mock_entry)
    assert seen == {"override": "warmstart", "release": "10.0"}  # held while the model was on its prior
    c = mock_entry.runtime_data
    assert not c.warmstart_pending and c.data.override is None
    assert hass.states.get("input_number.summer_threshold").state == "16.0"  # then planned with the model


def _now():
    from homeassistant.util import dt as dt_util

    return dt_util.utcnow()
