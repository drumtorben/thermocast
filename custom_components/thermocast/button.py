"""Button: re-learn all zone models from the recorder history (warm start)."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .entity import HouseEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities([WarmstartButton(entry.runtime_data, "warmstart")])


class WarmstartButton(HouseEntity, ButtonEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:history"

    async def async_press(self) -> None:
        await self.coordinator.async_warmstart(only_fresh=False)
