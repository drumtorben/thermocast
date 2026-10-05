"""Diagnostics download (Settings → Devices & services → Thermocast → ⋮ → Download diagnostics)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import ThermocastConfigEntry
from .model_view import async_export

TO_REDACT = {"latitude", "longitude"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ThermocastConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = await async_export(coordinator)
    data["actuator"] = coordinator.actuator.to_dict()
    data["control_enabled"] = coordinator.control_enabled
    return async_redact_data(data, TO_REDACT)
