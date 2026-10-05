"""Websocket API for the panel: push the view on every update; model/KPIs/export on demand."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .kpi_view import MAX_DAYS, async_kpis
from .model_view import async_export, async_model_view

_LOGGER = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_subscribe)
    websocket_api.async_register_command(hass, ws_model)
    websocket_api.async_register_command(hass, ws_kpis)
    websocket_api.async_register_command(hass, ws_export)


def _coordinator(hass: HomeAssistant):
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    return entries[0].runtime_data if entries else None


@websocket_api.websocket_command({vol.Required("type"): "thermocast/subscribe"})
@callback
def ws_subscribe(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Thermocast is not loaded")
        return

    @callback
    def forward() -> None:
        connection.send_message(websocket_api.event_message(msg["id"], {"view": coordinator.view}))

    connection.subscriptions[msg["id"]] = coordinator.async_add_listener(forward)
    connection.send_result(msg["id"])
    forward()


async def _respond(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any], build) -> None:
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_loaded", "Thermocast is not loaded")
        return
    try:
        result = await build(coordinator)
    except Exception as err:
        _LOGGER.exception("Thermocast: %s failed", msg["type"])
        connection.send_error(msg["id"], "failed", f"{type(err).__name__}: {err}")
        return
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "thermocast/model"})
@websocket_api.async_response
async def ws_model(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _respond(hass, connection, msg, async_model_view)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "thermocast/kpis",
        vol.Optional("days", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=MAX_DAYS)),
    }
)
@websocket_api.async_response
async def ws_kpis(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _respond(hass, connection, msg, lambda c: async_kpis(c, msg["days"]))


@websocket_api.websocket_command({vol.Required("type"): "thermocast/export"})
@websocket_api.async_response
async def ws_export(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    await _respond(hass, connection, msg, async_export)
