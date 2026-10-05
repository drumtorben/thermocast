"""Sidebar panel: static files + panel registration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN

PANEL_URL = "thermocast"
STATIC_URL = "/thermocast_static"
_STATIC_FLAG = f"{DOMAIN}_static_registered"


async def async_register_static(hass: HomeAssistant) -> None:
    """Static paths cannot be removed – register once per HA run."""
    if hass.data.get(_STATIC_FLAG):
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    hass.data[_STATIC_FLAG] = True


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    async_remove_panel(hass)  # a setup retry must not fail with "overwriting panel"
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="thermocast-panel",
        sidebar_title="Thermocast",
        sidebar_icon="mdi:home-thermometer",
        module_url=f"{STATIC_URL}/thermocast-panel.js?v={version}",
        require_admin=False,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
