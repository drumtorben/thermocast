"""Shared fixtures for the Home Assistant tests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.thermocast.const import (
    CONF_ACTIVE_FROM,
    CONF_ACTIVE_TO,
    CONF_COMFORT_BAND,
    CONF_COMFORT_TEMP,
    CONF_FLOW_TEMP_SENSOR,
    CONF_GAIN_ENTITIES,
    CONF_HEAT_TYPE,
    CONF_HEATING_ACTIVE,
    CONF_LEADS_RELEASE,
    CONF_NEIGHBOR_SENSORS,
    CONF_OUTDOOR_SENSOR,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    CONF_SURFACES,
    CONF_TEMP_SENSORS,
    CONF_WINDOW_ENTITIES,
    DOMAIN,
    SUBENTRY_ZONE,
)
from custom_components.thermocast.core.forecast import OPEN_METEO_URL

HOUSE_DATA = {
    CONF_OUTDOOR_SENSOR: "sensor.outdoor",
    CONF_FLOW_TEMP_SENSOR: "sensor.flow",
    CONF_HEATING_ACTIVE: "binary_sensor.heating_pump",
    CONF_RELEASE_ENTITY: "input_number.summer_threshold",
    CONF_RELEASE_ON: "16",
    CONF_RELEASE_OFF: "10",
}

ZONE_DATA = {
    CONF_HEAT_TYPE: "fbh",
    CONF_TEMP_SENSORS: ["sensor.living", "sensor.kitchen"],
    CONF_COMFORT_TEMP: 20.5,
    CONF_COMFORT_BAND: 0.3,
    CONF_ACTIVE_FROM: "00:00:00",
    CONF_ACTIVE_TO: "23:59:00",
    CONF_LEADS_RELEASE: True,
    CONF_SURFACES: [{"kind": "window", "azimuth": 90, "tilt": 90, "name": "East"}],
    CONF_NEIGHBOR_SENSORS: [],
    CONF_GAIN_ENTITIES: [],
    CONF_WINDOW_ENTITIES: [],
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Thermocast",
        data=dict(HOUSE_DATA),
        options={},
        subentries_data=[
            {
                "data": dict(ZONE_DATA),
                "subentry_id": "zone_eg",
                "subentry_type": SUBENTRY_ZONE,
                "title": "EG",
                "unique_id": None,
            }
        ],
    )


def open_meteo_payload(t_out: float, irr: float = 0.0) -> dict[str, Any]:
    """Hourly Open-Meteo-like payload around now (past day + 2 days)."""
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(days=1)
    times = [(start + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(72)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [t_out] * 72,
            "shortwave_radiation": [irr] * 72,
            "global_tilted_irradiance": [irr] * 72,
        }
    }


@pytest.fixture
def mock_open_meteo(aioclient_mock: AiohttpClientMocker):
    """Cold, dark weather by default; returns a setter for other temperatures."""

    def _set(t_out: float, irr: float = 0.0) -> None:
        aioclient_mock.clear_requests()
        aioclient_mock.get(OPEN_METEO_URL, json=open_meteo_payload(t_out, irr))

    _set(-5.0)
    return _set
