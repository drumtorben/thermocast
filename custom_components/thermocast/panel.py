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


def static_base(version: str) -> str:
    """One path per release: the panel's modules import each other relatively (tc-*.js, i18n.js) and are
    served without cache headers – under an unchanged URL browsers kept stale modules after an update."""
    return f"{STATIC_URL}/{version}"


async def async_register_static(hass: HomeAssistant, version: str) -> None:
    """Static paths cannot be removed – register once per HA run and version."""
    done: set[str] = hass.data.setdefault(_STATIC_FLAG, set())
    if version in done:
        return
    await hass.http.async_register_static_paths(
        [StaticPathConfig(static_base(version), str(Path(__file__).parent / "frontend"), cache_headers=False)]
    )
    done.add(version)


async def async_register_panel(hass: HomeAssistant, version: str) -> None:
    async_remove_panel(hass)  # a setup retry must not fail with "overwriting panel"
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="thermocast-panel",
        sidebar_title="Thermocast",
        sidebar_icon="mdi:home-thermometer",
        module_url=f"{static_base(version)}/thermocast-panel.js",
        require_admin=False,
    )


def async_remove_panel(hass: HomeAssistant) -> None:
    frontend.async_remove_panel(hass, PANEL_URL, warn_if_unknown=False)
