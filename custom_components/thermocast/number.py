"""Numbers: vacation floor and preheat lead."""
from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .const import VACATION_FLOOR_MAX, VACATION_FLOOR_MIN, VACATION_LEAD_MAX_H
from .entity import HouseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([VacationFloor(coordinator, "vacation_floor"), VacationLead(coordinator, "vacation_lead")])


class _VacationNumber(HouseEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _field: str

    @property
    def available(self) -> bool:
        return True  # an input: settable even while an update fails

    @property
    def native_value(self) -> float:
        return getattr(self.coordinator.vacation, self._field)

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_vacation(**{self._field: float(value)})
        self.async_write_ha_state()


class VacationFloor(_VacationNumber):
    """House-wide lower bound while away (lowers each zone's base, never raises it)."""

    _field = "floor"
    _attr_icon = "mdi:thermometer-low"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = VACATION_FLOOR_MIN
    _attr_native_max_value = VACATION_FLOOR_MAX
    _attr_native_step = 0.5


class VacationLead(_VacationNumber):
    """Hours before the return in which the normal base applies again (the house heats up)."""

    _field = "lead_h"
    _attr_icon = "mdi:home-clock"
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_native_min_value = 0
    _attr_native_max_value = VACATION_LEAD_MAX_H
    _attr_native_step = 1
