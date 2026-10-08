"""Sidebar panel, static files and the websocket subscription."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.frontend import DATA_PANELS
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.thermocast.core.model import HourRecord, Prediction

from .test_init import _setup_entry, _setup_states


async def test_panel_registered_and_removed(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    panel = hass.data[DATA_PANELS]["thermocast"]
    assert panel.sidebar_title == "Thermocast"
    # every release gets its own path: browsers cannot keep stale modules (tc-*.js, i18n.js) after an update
    module_url = panel.config["_panel_custom"]["module_url"]
    assert module_url.startswith("/thermocast_static/") and module_url.endswith("/thermocast-panel.js")
    base = module_url.rsplit("/", 1)[0]
    assert base != "/thermocast_static"
    client = await hass_client()
    resp = await client.get(f"{base}/lit.js")
    assert resp.status == 200
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    assert "thermocast" not in hass.data[DATA_PANELS]


async def test_diagnostics_while_not_loaded(hass: HomeAssistant, mock_entry, mock_open_meteo) -> None:
    """Downloading diagnostics during a reload must not raise (500)."""
    from custom_components.thermocast.diagnostics import async_get_config_entry_diagnostics

    mock_entry.add_to_hass(hass)
    data = await async_get_config_entry_diagnostics(hass, mock_entry)
    assert data["loaded"] is False and data["zones"]


async def test_subscribe_pushes_view(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_ws_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "thermocast/subscribe"})
    assert (await ws.receive_json())["success"] is True
    first = await ws.receive_json()
    assert first["event"]["view"]["version"] == 1

    await mock_entry.runtime_data.async_refresh()
    await hass.async_block_till_done(wait_background_tasks=True)
    # every update: the decision right away, then the rebuilt plan
    decision = await ws.receive_json()
    rebuilt = await ws.receive_json()
    assert decision["event"]["view"]["version"] == 1 and rebuilt["event"]["view"]["version"] == 1
    assert rebuilt["event"]["view"]["generated_at"] >= decision["event"]["view"]["generated_at"]

    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    gone = await ws.receive_json()
    assert gone["event"]["view"] == {"error": "unloaded"}


async def test_model_kpis_export_commands(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_ws_client) -> None:
    mock_open_meteo(5.0)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    zone_rt = mock_entry.runtime_data.zones["zone_eg"]
    h0 = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(hours=3)
    for i in range(2):  # two closed hours, and a 1-h forecast issued before the second one
        rec = HourRecord(temp=20.4 - 0.1 * i, t_out=5.0, irr={"90_90": 0.0}, q=0.0)
        zone_rt.log.append({"t": (h0 + timedelta(hours=i)).isoformat(), "rec": rec.to_dict(),
                            "temp_next": 20.3 - 0.1 * i, "err": 0.02})
    zone_rt.flog.record(h0, Prediction(mean=[20.32] * 24, std=[0.1] * 24))

    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "thermocast/model"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    zone = msg["result"]["zones"][0]
    assert zone["name"] == "EG" and zone["metrics"]["n_logged"] == 2
    assert len(zone["hours"]) == 2 and zone["hindcast"][0] is not None
    assert zone["metrics"]["horizons"]["1"]["n"] >= 1  # the 11:00 forecast for 12:00 was checked
    keys = {p["key"] for p in zone["params"]}
    assert {"tau", "heat", "base", "sun:90_90:window"} <= keys
    assert zone["params"][0]["history"] == [] and zone["heat_lags"]  # snapshots start with the first closed hour

    await ws.send_json_auto_id({"type": "thermocast/kpis", "days": 14})
    msg = await ws.receive_json()
    assert msg["success"], msg
    assert len(msg["result"]["days"]) == 14 and "recorder" in msg["result"]["missing"]

    await ws.send_json_auto_id({"type": "thermocast/export"})
    msg = await ws.receive_json()
    assert msg["success"], msg
    export = msg["result"]
    assert export["zones"]["zone_eg"]["log"] and export["view"]["version"] == 1
    assert export["model_view"]["zones"][0]["id"] == "zone_eg"


async def test_subscribe_without_entry(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_ws_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    assert await hass.config_entries.async_unload(mock_entry.entry_id)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "thermocast/subscribe"})
    msg = await ws.receive_json()
    assert msg["success"] is False and msg["error"]["code"] == "not_loaded"


async def test_brand_icon_is_served(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_client) -> None:
    from homeassistant.setup import async_setup_component

    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    assert await async_setup_component(hass, "brands", {})
    client = await hass_client()
    for image in ("icon.png", "icon@2x.png", "dark_logo.png"):
        resp = await client.get(f"/api/brands/integration/thermocast/{image}")
        assert resp.status == 200, image
        assert (await resp.read())[:8] == b"\x89PNG\r\n\x1a\n"
