"""Zone bounds for charge and coast: floor, upper bound, quiet hours, charge cap (coordinator helpers)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.thermocast.const import (
    CONF_BASE_TEMP,
    CONF_BT_CONTROL,
    CONF_BT_ENTITY,
    CONF_COMFORT_HIGH,
    CONF_HEAT_TYPE,
    CONF_PRECHARGE_H,
    CONF_QUIET_FROM,
    CONF_QUIET_TO,
    CONF_STARTS_WEIGHT,
    SUBENTRY_ZONE,
)
from custom_components.thermocast.coordinator import (
    ZoneRuntime,
    zone_base,
    zone_charge_cap,
    zone_charge_target,
    zone_floor,
    zone_high,
    zone_quiet,
)
from custom_components.thermocast.core.model import OnlineZoneModel, ZoneSpec

from .conftest import ZONE_DATA


def _z(**cfg) -> ZoneRuntime:
    data = {**ZONE_DATA, "active_from": "06:00:00", "active_to": "22:00:00", **cfg}
    return ZoneRuntime("z", "Z", data, OnlineZoneModel(ZoneSpec()))


def _local(hass: HomeAssistant, hour: int, minute: int = 0) -> datetime:
    from homeassistant.util import dt as dt_util

    return datetime(2026, 10, 6, hour, minute, tzinfo=dt_util.get_time_zone(hass.config.time_zone)).astimezone(UTC)


async def test_defaults_for_existing_zones(hass: HomeAssistant) -> None:
    z = _z()  # comfort 20.5, band 0.3, no new fields
    assert zone_high(z) == 21.5 and zone_base(z) == 18.5
    assert zone_floor(z, _local(hass, 12)) == 20.2  # comfort time: comfort − band
    assert zone_floor(z, _local(hass, 3)) == 18.5  # otherwise: the floor
    assert zone_charge_cap(z, _local(hass, 12)) is None  # no BT control -> uncapped


async def test_quiet_hours_over_midnight(hass: HomeAssistant) -> None:
    z = _z(**{CONF_QUIET_FROM: "19:00:00", CONF_QUIET_TO: "07:00:00", CONF_BT_CONTROL: True,
              CONF_BT_ENTITY: "climate.bt", CONF_COMFORT_HIGH: 21.0, CONF_BASE_TEMP: 17.0})
    assert zone_quiet(z, _local(hass, 23)) and zone_quiet(z, _local(hass, 3))
    assert not zone_quiet(z, _local(hass, 12)) and not zone_quiet(z, _local(hass, 7))
    assert not zone_quiet(z, _local(hass, 18, 50)) and zone_quiet(z, _local(hass, 18, 50), lead=timedelta(minutes=15))
    assert zone_charge_cap(z, _local(hass, 12)) == 21.0  # charge to the upper bound
    assert zone_charge_cap(z, _local(hass, 23)) == 17.0  # quiet: the thermostat stays at the floor


async def test_charge_target_outside_comfort(hass: HomeAssistant) -> None:
    """Outside comfort time a block charges the zone only if comfort starts within precharge_h, else to the base."""
    z = _z(**{CONF_BT_CONTROL: True, CONF_BT_ENTITY: "climate.bt", CONF_COMFORT_HIGH: 22.5, CONF_BASE_TEMP: 17.0})
    assert zone_charge_target(z, _local(hass, 12)) == 22.5  # comfort time
    assert zone_charge_target(z, _local(hass, 2)) == 22.5  # comfort starts at 06:00, in 4 h
    assert zone_charge_target(z, _local(hass, 22, 30)) == 22.5  # next comfort in 7.5 h
    z.schedule_plan = {"tuesday": [{"from": "08:00:00", "to": "15:00:00"}]}  # 2026-10-06 is a Tuesday
    saturday = _local(hass, 10) - timedelta(days=3)  # 2026-10-03, no comfort until Tuesday
    assert zone_charge_target(z, saturday) == 17.0
    assert zone_charge_cap(z, saturday) == 17.0  # the planner sees the same target
    assert zone_charge_target(z, _local(hass, 7)) == 22.5  # Tuesday morning, comfort in 1 h
    assert zone_charge_target(z, _local(hass, 16)) == 17.0  # next comfort a week away
    z.precharge_h = 48  # house option: charge two days ahead
    assert zone_charge_target(z, saturday) == 17.0  # Tuesday 08:00 is still 70 h away
    assert zone_charge_target(z, saturday + timedelta(days=1)) == 22.5  # Sunday 10:00, comfort in 46 h
    z.precharge_h = 0  # only in comfort time
    assert zone_charge_target(z, _local(hass, 7)) == 17.0 and zone_charge_target(z, _local(hass, 8)) == 22.5


async def test_open_window_neither_leads_nor_takes_heat(hass: HomeAssistant) -> None:
    from custom_components.thermocast.coordinator import build_plan_inputs
    from custom_components.thermocast.core.forecast import Forecast

    z = _z(**{CONF_BT_CONTROL: True, CONF_BT_ENTITY: "climate.bt", CONF_COMFORT_HIGH: 22.5, CONF_BASE_TEMP: 18.0,
              "window_entities": ["binary_sensor.window"]})
    hass.states.async_set("sensor.living", "20.0")
    hass.states.async_set("sensor.kitchen", "20.0")
    t0 = datetime(2026, 10, 7, 6, tzinfo=UTC)
    fc = Forecast(times=[t0 + timedelta(hours=h) for h in range(8)], t_out=[5.0] * 8, irr={}, fetched_at=t0)
    hass.states.async_set("binary_sensor.window", "off")
    closed = build_plan_inputs(hass, {"z": z}, fc, 0, 6)[0]
    assert closed.leads_release and closed.charge_cap[0] == 22.5
    hass.states.async_set("binary_sensor.window", "on")
    airing = build_plan_inputs(hass, {"z": z}, fc, 0, 6)[0]
    assert not airing.leads_release and set(airing.charge_cap) == {18.0}


async def test_tail_trim_reads_the_burner_starts_counter(hass: HomeAssistant) -> None:
    from types import SimpleNamespace

    from custom_components.thermocast.coordinator import ThermocastCoordinator

    now = datetime(2026, 10, 7, 1, 13, tzinfo=UTC)
    hass.states.async_set("sensor.burner_starts", "70")
    state = hass.states.get("sensor.burner_starts")
    object.__setattr__(state, "last_changed", now - timedelta(minutes=31))  # last start 31 min ago
    fake = SimpleNamespace(
        hass=hass, _started=now - timedelta(hours=5),
        config_entry=SimpleNamespace(options={"anti_cycle_minutes": 45, "burner_starts_entity": "sensor.burner_starts"}),
    )
    end = now + timedelta(minutes=15)  # the restart at +14 min would run only 1 min
    assert ThermocastCoordinator._trim_tail(fake, now, end)
    fake.config_entry.options["anti_cycle_minutes"] = 0  # option off
    assert not ThermocastCoordinator._trim_tail(fake, now, end)
    fake.config_entry.options["anti_cycle_minutes"] = 45
    fake._started = now - timedelta(minutes=30)  # counter only restored at startup
    assert not ThermocastCoordinator._trim_tail(fake, now, end)


async def test_quiet_from_schedule_plan_and_window(hass: HomeAssistant) -> None:
    """Quiet when the fixed window OR the schedule says so; the 15-min lead also looks into the schedule."""
    z = _z(**{CONF_QUIET_FROM: "19:00:00", CONF_QUIET_TO: "07:00:00"})
    z.quiet_plan = {d: [{"from": "12:30:00", "to": "14:30:00"}] for d in
                    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")}
    assert zone_quiet(z, _local(hass, 13)) and zone_quiet(z, _local(hass, 23))
    assert not zone_quiet(z, _local(hass, 15)) and not zone_quiet(z, _local(hass, 12, 20))
    assert zone_quiet(z, _local(hass, 12, 20), lead=timedelta(minutes=15))


async def test_zone_flow_new_fields_and_validation(
    hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo
) -> None:
    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_USER}
    )
    base = {**ZONE_DATA, CONF_NAME: "Büro", CONF_HEAT_TYPE: "radiator", CONF_SURFACES_EMPTY: []}
    bad = {**base, CONF_COMFORT_HIGH: 20.0}  # not above comfort 20.5
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["errors"] == {"base": "invalid_bounds"}
    bad = {**base, CONF_BT_CONTROL: True}  # no BT entity
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], bad)
    assert result["errors"] == {CONF_BT_ENTITY: "bt_entity_missing"}
    good = {**base, CONF_BT_CONTROL: True, CONF_BT_ENTITY: "climate.bt", CONF_COMFORT_HIGH: 21.5,
            CONF_BASE_TEMP: 18.0, CONF_QUIET_FROM: "19:00:00", CONF_QUIET_TO: "07:00:00"}
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], good)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    sub = next(s for s in mock_entry.subentries.values() if s.title == "Büro")
    assert sub.data[CONF_BT_CONTROL] is True and sub.data[CONF_QUIET_FROM] == "19:00:00"


async def test_reconfigure_keeps_old_zone_valid(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    mock_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.subentries.async_init(
        (mock_entry.entry_id, SUBENTRY_ZONE), context={"source": SOURCE_RECONFIGURE, "subentry_id": "zone_eg"}
    )
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_COMFORT_HIGH].description == {"suggested_value": 21.5}  # default shown, not stored yet


async def test_starts_weight_option(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo) -> None:
    from custom_components.thermocast.const import (
        CONF_CONFIDENCE_Z,
        CONF_FORGETTING,
        CONF_MAX_SWITCHES,
        CONF_MIN_BLOCK_H,
        CONF_MIN_PAUSE_H,
    )

    mock_entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(mock_entry.entry_id)
    keys = {str(k): k for k in result["data_schema"].schema}
    assert keys[CONF_STARTS_WEIGHT].default() == 80
    assert keys[CONF_PRECHARGE_H].default() == 12
    opts = {CONF_MIN_BLOCK_H: 3, CONF_MIN_PAUSE_H: 2, CONF_MAX_SWITCHES: 12, CONF_FORGETTING: 0.996,
            CONF_CONFIDENCE_Z: 1.0, CONF_STARTS_WEIGHT: 50, CONF_PRECHARGE_H: 24}
    await hass.config_entries.options.async_configure(result["flow_id"], opts)
    assert mock_entry.options[CONF_STARTS_WEIGHT] == 50 and mock_entry.options[CONF_PRECHARGE_H] == 24
    assert await hass.config_entries.async_setup(mock_entry.entry_id)
    await hass.async_block_till_done()
    assert {z.precharge_h for z in mock_entry.runtime_data.zones.values()} == {24}


CONF_SURFACES_EMPTY = "surfaces"
