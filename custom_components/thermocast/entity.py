"""Shared entity base classes."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ThermocastCoordinator, ZoneResult


class HouseEntity(CoordinatorEntity[ThermocastCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ThermocastCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            entry_type=DeviceEntryType.SERVICE,
        )


class ZoneEntity(CoordinatorEntity[ThermocastCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ThermocastCoordinator, subentry_id: str, title: str, key: str) -> None:
        super().__init__(coordinator)
        self.subentry_id = subentry_id
        self._attr_translation_key = key
        self._attr_unique_id = f"{subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry_id)},
            name=title,
            entry_type=DeviceEntryType.SERVICE,
            via_device_id=coordinator.house_device_id,
        )

    @property
    def zone(self) -> ZoneResult | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.zones.get(self.subentry_id)

    @property
    def available(self) -> bool:
        return super().available and self.zone is not None
