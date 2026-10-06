"""Thermocast – predictive heating release with online-learned room models."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

from . import panel, websocket_api
from .const import DOMAIN, PLATFORMS, SUBENTRY_ZONE
from .coordinator import RELOADING, ThermocastCoordinator

type ThermocastConfigEntry = ConfigEntry[ThermocastCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    websocket_api.async_register(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ThermocastConfigEntry) -> bool:
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    # house device first: zone devices link to it via its registry id
    house = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        entry_type=dr.DeviceEntryType.SERVICE,
    )
    hass.data.setdefault(DOMAIN, {}).pop(RELOADING, None)  # a reload flag that found no unload is stale
    coordinator = ThermocastCoordinator(hass, entry)
    coordinator.house_device_id = house.id
    await coordinator.async_load()
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    version = str((await async_get_integration(hass, DOMAIN)).version)
    await panel.async_register_static(hass, version)
    await panel.async_register_panel(hass, version)
    # a zone added while this setup was running (e.g. two zones saved quickly) needs another reload
    configured = {sid for sid, sub in entry.subentries.items() if sub.subentry_type == SUBENTRY_ZONE}
    if configured != set(coordinator.zones):
        _self_reload(hass, entry)
    elif any(z.model.n_updates == 0 for z in coordinator.zones.values()):
        # new zones learn from the recorder history instead of starting from the prior
        entry.async_create_background_task(
            hass, coordinator.async_warmstart(only_fresh=True), "thermocast_warmstart"
        )
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ThermocastConfigEntry) -> bool:
    coordinator: ThermocastCoordinator = entry.runtime_data
    # never leave the heating blocked when the integration goes away
    await coordinator.async_shutdown_failsafe()
    panel.async_remove_panel(hass)
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload(hass: HomeAssistant, entry: ThermocastConfigEntry) -> None:
    """Options or zones (subentries) changed."""
    _self_reload(hass, entry)


def _self_reload(hass: HomeAssistant, entry: ThermocastConfigEntry) -> None:
    """Reload after a config change: the unload must not hand the heating back (it would switch the
    boiler on and off again – EEPROM writes, an extra burner start)."""
    hass.data.setdefault(DOMAIN, {})[RELOADING] = True
    hass.config_entries.async_schedule_reload(entry.entry_id)
