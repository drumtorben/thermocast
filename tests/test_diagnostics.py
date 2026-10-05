"""Diagnostics download and the repair issue for a lasting fail-safe."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry

from custom_components.thermocast.const import DOMAIN
from custom_components.thermocast.core.forecast import OPEN_METEO_URL

from .conftest import open_meteo_payload
from .test_init import _setup_entry, _setup_states


async def test_diagnostics(hass: HomeAssistant, mock_entry, mock_open_meteo, hass_client) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    diag = await get_diagnostics_for_config_entry(hass, hass_client, mock_entry)
    assert diag["zones"]["zone_eg"]["model"]["names"]
    assert diag["actuator"]["writes_today"] == 0 and diag["control_enabled"] is False
    assert diag["view"]["version"] == 1


async def test_lasting_failsafe_raises_and_clears_an_issue(
    hass: HomeAssistant, mock_entry, aioclient_mock, freezer
) -> None:
    aioclient_mock.get(OPEN_METEO_URL, status=500)
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "failsafe") is None  # not after one failed fetch

    for _ in range(5):  # > 1 h of fail-safe
        freezer.tick(timedelta(minutes=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done(wait_background_tasks=True)
    issue = issues.async_get_issue(DOMAIN, "failsafe")
    assert issue is not None and issue.translation_placeholders == {"reason": "no_forecast"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(OPEN_METEO_URL, json=open_meteo_payload(5.0))
    freezer.tick(timedelta(minutes=15))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert issues.async_get_issue(DOMAIN, "failsafe") is None
