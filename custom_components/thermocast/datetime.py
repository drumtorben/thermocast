"""Date/time: vacation departure and return."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .entity import HouseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([
        VacationTime(coordinator, "vacation_start", "start"),
        VacationTime(coordinator, "vacation_end", "end"),
    ])


class VacationTime(HouseEntity, DateTimeEntity):
    _attr_icon = "mdi:airplane"

    def __init__(self, coordinator, key: str, field: str) -> None:
        super().__init__(coordinator, key)
        self._field = field

    @property
    def available(self) -> bool:
        return True  # an input: settable even while an update fails

    @property
    def native_value(self) -> datetime | None:
        return getattr(self.coordinator.vacation, self._field)

    async def async_set_value(self, value: datetime) -> None:
        await self.coordinator.async_set_vacation(**{self._field: value})
        self.async_write_ha_state()
