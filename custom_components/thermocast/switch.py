"""Switch: control enabled (off = observe mode, heating always released)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .entity import HouseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([ControlSwitch(entry.runtime_data, "control_enabled")])


class ControlSwitch(HouseEntity, SwitchEntity):
    _attr_icon = "mdi:robot"

    @property
    def is_on(self) -> bool:
        return self.coordinator.control_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_control(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_control(False)
        self.async_write_ha_state()
