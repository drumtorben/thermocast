"""Binary sensors: overall heating decision and per-zone demand."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .entity import HouseEntity, ZoneEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([HeatingReleaseSensor(coordinator, "heating_release")])
    for sid, zone in coordinator.zones.items():
        async_add_entities([ZoneDemandSensor(coordinator, sid, zone.title, "heat_demand")], config_subentry_id=sid)


class HeatingReleaseSensor(HouseEntity, BinarySensorEntity):
    """What the planner wants right now (also in observe mode)."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.want_heat if self.coordinator.data else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        if d is None:
            return {}
        return {
            "control_enabled": d.control_enabled,
            "applied_release": d.release_on if d.control_enabled else None,
            "failsafe_reason": d.failsafe_reason,
        }


class ZoneDemandSensor(ZoneEntity, BinarySensorEntity):
    """Zone would drop below its comfort band within the horizon without heating."""

    _attr_device_class = BinarySensorDeviceClass.HEAT

    @property
    def is_on(self) -> bool | None:
        return self.zone.demand if self.zone else None
