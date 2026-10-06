"""Setup, entities, fail-safe and actuation inside Home Assistant."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.thermocast.const import DOMAIN


async def _setup_states(hass: HomeAssistant, temp: float = 20.4, release: float = 16) -> None:
    assert await async_setup_component(
        hass,
        "input_number",
        {"input_number": {"summer_threshold": {"min": 5, "max": 30, "step": 1, "initial": release}}},
    )
    hass.states.async_set("sensor.outdoor", "-5.0", {"device_class": "temperature"})
    hass.states.async_set("sensor.flow", "35.0", {"device_class": "temperature"})
    hass.states.async_set("binary_sensor.heating_pump", "off")
    hass.states.async_set("sensor.living", str(temp), {"device_class": "temperature"})
    hass.states.async_set("sensor.kitchen", str(temp), {"device_class": "temperature"})


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED


def _eid(hass: HomeAssistant, unique_id: str, platform: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, f"{platform} {unique_id} not registered"
    return entity_id


async def test_setup_with_null_temperatures_in_stored_log(
    hass: HomeAssistant, mock_entry, mock_open_meteo, hass_storage
) -> None:
    """Regression: a warm-start log with gaps (temp = null) crashed the setup in the σ calibration."""
    from homeassistant.util import dt as dt_util

    from custom_components.thermocast.coordinator import STORAGE_VERSION

    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    rec = {"t_out": 8.0, "temp": None, "irr": [], "q": 0.0, "neighbors": [], "gains": [], "valid": True}
    hass_storage[f"{DOMAIN}.{mock_entry.entry_id}.logs"] = {
        "version": STORAGE_VERSION,
        "key": f"{DOMAIN}.{mock_entry.entry_id}.logs",
        "data": {"zones": {"zone_eg": {
            "log": [{"t": hour.isoformat(), "rec": rec, "temp_next": None, "err": None}],
            "flog": {hour.isoformat(): {"1": [None, None]}},
            "params": [],
        }}},
    }
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)


async def test_setup_creates_entities(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    eid = mock_entry.entry_id

    switch = hass.states.get(_eid(hass, f"{eid}_control_enabled", "switch"))
    assert switch.state == STATE_OFF  # observe mode by default

    release = hass.states.get(_eid(hass, f"{eid}_heating_release", "binary_sensor"))
    assert release.state in (STATE_ON, STATE_OFF)
    assert release.attributes["failsafe_reason"] is None

    hass.states.get(_eid(hass, f"{eid}_next_block", "sensor"))

    fc = hass.states.get(_eid(hass, "zone_eg_forecast_min", "sensor"))
    assert fc is not None
    assert len(fc.attributes["forecast"]) == 24
    assert hass.states.get(_eid(hass, "zone_eg_model_error", "sensor")) is not None
    assert hass.states.get(_eid(hass, "zone_eg_heat_demand", "binary_sensor")) is not None

    # zone entities live on their own device, linked to the subentry
    ent = er.async_get(hass).async_get(_eid(hass, "zone_eg_forecast_min", "sensor"))
    assert ent.config_subentry_id == "zone_eg"
    devices = dr.async_get(hass)
    device = devices.async_get(ent.device_id)
    assert device.name == "EG"
    house = devices.async_get_device_by_identifier((DOMAIN, eid), eid)
    assert device.via_device_id == house.id


async def test_no_deprecation_warnings(hass: HomeAssistant, mock_entry, mock_open_meteo, caplog) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    assert "deprecated" not in caplog.text


async def test_observe_mode_never_writes(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    mock_open_meteo(18.0)  # warm: planner does not want heat
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    release = hass.states.get(_eid(hass, f"{mock_entry.entry_id}_heating_release", "binary_sensor"))
    assert release.state == STATE_OFF
    assert hass.states.get("input_number.summer_threshold").state == "16.0"
    assert mock_entry.runtime_data.data.override == "observe"


async def test_reload_does_not_flip_the_release(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """Every config change reloads the entry – that must not switch the boiler on and off again."""
    mock_open_meteo(18.0)
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    switch = _eid(hass, f"{mock_entry.entry_id}_control_enabled", "switch")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_number.summer_threshold").state == "10.0"
    writes = []
    hass.bus.async_listen("state_changed", lambda e: e.data["entity_id"] == "input_number.summer_threshold"
                          and writes.append(e.data["new_state"].state))

    # an options change reloads the entry through the update listener
    hass.config_entries.async_update_entry(mock_entry, options={**mock_entry.options, "starts_weight": 60})
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_entry.runtime_data.config_entry.options["starts_weight"] == 60  # reloaded
    assert writes == [] and hass.states.get("input_number.summer_threshold").state == "10.0"


async def test_control_blocks_when_warm_and_unload_releases(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    mock_open_meteo(18.0)
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    switch = _eid(hass, f"{mock_entry.entry_id}_control_enabled", "switch")

    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_ON
    assert hass.states.get("input_number.summer_threshold").state == "10.0"
    coordinator = mock_entry.runtime_data
    assert coordinator.data.override is None
    assert coordinator.data.planner_heat is False

    # fail-safe: unloading (disable / remove) hands the heating back
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("input_number.summer_threshold").state == "16.0"


async def test_control_off_releases(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    mock_open_meteo(18.0)
    await _setup_states(hass, temp=22.0)
    await _setup_entry(hass, mock_entry)
    switch = _eid(hass, f"{mock_entry.entry_id}_control_enabled", "switch")
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get("input_number.summer_threshold").state == "10.0"

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(switch).state == STATE_OFF
    assert hass.states.get("input_number.summer_threshold").state == "16.0"


async def test_missing_leading_sensor_failsafe(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    hass.states.async_set("sensor.living", "unavailable")
    hass.states.async_set("sensor.kitchen", "unavailable")
    await _setup_entry(hass, mock_entry)
    release = hass.states.get(_eid(hass, f"{mock_entry.entry_id}_heating_release", "binary_sensor"))
    assert release.state == STATE_ON
    assert release.attributes["failsafe_reason"] == "no_temperature:EG"


async def test_hour_close_survives_changed_sensor_layout(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    """Regression: the running hour was collected before neighbours were added -> IndexError every 15 min."""
    from custom_components.thermocast.const import CONF_NEIGHBOR_SENSORS

    freezer.move_to("2026-10-04 10:05:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass)
    hass.states.async_set("sensor.hall", "19.0", {"device_class": "temperature"})
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_subentry(
        mock_entry, mock_entry.subentries["zone_eg"],
        data={**mock_entry.subentries["zone_eg"].data, CONF_NEIGHBOR_SENSORS: ["sensor.hall"]},
    )
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    z = mock_entry.runtime_data.zones["zone_eg"]
    z.acc["neighbors"] = [[]] * len(z.acc["neighbors"])  # samples from the old layout (no neighbour)

    freezer.move_to("2026-10-04 11:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert mock_entry.runtime_data.last_update_success
    assert z.model.n_updates == 1 and len(z.log) == 1
    assert z.log.to_list()[0]["rec"]["neighbors"] == [20.4]  # no sample -> falls back to the zone temperature


async def test_dhw_charging_is_not_space_heating(hass: HomeAssistant, mock_entry, mock_open_meteo, freezer) -> None:
    """Combi boiler: the pump runs with ~70 °C flow for hot water – that must not be learned as room heating."""
    from custom_components.thermocast.const import CONF_DHW_ENTITY

    freezer.move_to("2026-10-04 10:05:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass)
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("binary_sensor.dhw_charging", "on")
    hass.states.async_set("sensor.flow", "70.0", {"device_class": "temperature"})
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(mock_entry, options={CONF_DHW_ENTITY: "binary_sensor.dhw_charging"})
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("binary_sensor.dhw_charging", "off")  # hour 11: space heating, the flow counts
    freezer.move_to("2026-10-04 11:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    z = mock_entry.runtime_data.zones["zone_eg"]
    assert z.log.to_list()[0]["rec"]["q"] == 0.0  # hour 10 was sampled during the charge

    freezer.move_to("2026-10-04 12:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert z.log.to_list()[-1]["rec"]["q"] > 40


async def test_radiator_heats_only_while_its_thermostat_heats(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer
) -> None:
    """TRVs report no valve position – the thermostat's hvac_action decides whether the radiator gets heat."""
    from custom_components.thermocast.const import CONF_HEAT_TYPE, CONF_VALVE_ENTITY

    freezer.move_to("2026-10-04 10:05:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass)
    hass.states.async_set("binary_sensor.heating_pump", "on")
    hass.states.async_set("sensor.flow", "45.0", {"device_class": "temperature"})
    hass.states.async_set("climate.trv", "heat", {"hvac_action": "idle"})
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_subentry(
        mock_entry, mock_entry.subentries["zone_eg"],
        data={**mock_entry.subentries["zone_eg"].data, CONF_HEAT_TYPE: "radiator", CONF_VALVE_ENTITY: "climate.trv"},
    )
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()

    hass.states.async_set("climate.trv", "heat", {"hvac_action": "heating"})
    freezer.move_to("2026-10-04 11:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    z = mock_entry.runtime_data.zones["zone_eg"]
    assert z.log.to_list()[0]["rec"]["q"] == 0.0  # pump ran, but the valve stayed closed

    freezer.move_to("2026-10-04 12:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert z.log.to_list()[-1]["rec"]["q"] == pytest.approx(45.0 - 20.4)


async def test_hour_close_learns_and_persists(
    hass: HomeAssistant, mock_entry, mock_open_meteo, freezer, hass_storage
) -> None:
    freezer.move_to("2026-10-04 10:05:00+00:00")
    mock_open_meteo(5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    err_sensor = _eid(hass, "zone_eg_model_error", "sensor")
    assert hass.states.get(err_sensor).attributes["updates"] == 0

    for _ in range(5):  # 15-min ticks into the next hour
        freezer.tick(timedelta(minutes=15))
        hass.states.async_set("sensor.living", "20.2", {"device_class": "temperature"})
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get(err_sensor).attributes["updates"] == 1
    freezer.tick(timedelta(seconds=61))  # debounced saves
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{mock_entry.entry_id}"]["data"]
    assert stored["zones"]["zone_eg"]["model"]
    assert stored["control_enabled"] is False
    pending = stored["zones"]["zone_eg"]["pending"]
    assert pending["hour"] == "2026-10-04T11:00:00+00:00" and pending["acc"]["q"]
    # hour log for the model-quality tab (separate, larger store)
    zone = hass_storage[f"{DOMAIN}.{mock_entry.entry_id}.logs"]["data"]["zones"]["zone_eg"]
    assert len(zone["log"]) == 1
    entry = zone["log"][0]
    assert entry["t"] == "2026-10-04T10:00:00+00:00"
    assert entry["rec"]["temp"] == 20.4 and entry["temp_next"] == pytest.approx(20.3)  # (20.2 + 20.4) / 2
    assert entry["err"] is not None
    assert zone["params"][0]["date"] == "2026-10-04" and "loss" in zone["params"][0]["theta"]
    # the operational forecast is logged by the view builder every hour
    assert zone["flog"]

    # the view shows the causes of the logged hour (hindcast)
    view = mock_entry.runtime_data.view
    i10 = view["hours"].index("2026-10-04T10:00:00+00:00")
    assert view["zones"][0]["contrib"][i10]
    assert view["zones"][0]["forecast6"][i10] is None  # nothing was predicted 6 h before

    # survives a reload – including the running hour, which then closes normally
    assert await hass.config_entries.async_reload(mock_entry.entry_id)
    await hass.async_block_till_done()
    zr = mock_entry.runtime_data.zones["zone_eg"]
    assert len(zr.log) == 1
    assert zr.pending_hour.isoformat() == "2026-10-04T11:00:00+00:00" and zr.acc["q"]
    freezer.move_to("2026-10-04 12:05:00+00:00")
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(zr.log) == 2 and zr.log.to_list()[-1]["t"] == "2026-10-04T11:00:00+00:00"
    assert zr.model.n_updates == 2  # the hour across the reload was learned


async def test_control_since_is_set_once(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    coordinator = mock_entry.runtime_data
    assert coordinator.control_since is None
    await coordinator.async_set_control(True)
    first = coordinator.control_since
    await coordinator.async_set_control(False)
    await coordinator.async_set_control(True)
    assert first is not None and coordinator.control_since == first


async def test_no_forecast_failsafe(hass: HomeAssistant, mock_entry, aioclient_mock) -> None:
    aioclient_mock.get("https://api.open-meteo.com/v1/forecast", status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    release = hass.states.get(_eid(hass, f"{mock_entry.entry_id}_heating_release", "binary_sensor"))
    assert release.state == STATE_ON
    assert release.attributes["failsafe_reason"] == "no_forecast"


def test_missing_vs_zero():
    from custom_components.thermocast.coordinator import _or

    assert _or(0.0, 20.5) == 0.0  # a neighbour at 0 °C is a value, not "missing"
    assert _or(None, 20.5) == 20.5
