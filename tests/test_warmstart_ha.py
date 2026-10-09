"""Warm start against real recorder statistics (needs the recorder fixture before `hass`)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.thermocast.const import DOMAIN
from custom_components.thermocast.core.forecast import OPEN_METEO_URL

from .synthetic import simulate
from .test_init import _setup_entry, _setup_states

DAYS = 31


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations: None) -> None:
    """Override the conftest fixture: the recorder must be set up before hass."""


def _meta(statistic_id: str) -> dict:
    return {
        "source": "recorder", "statistic_id": statistic_id, "name": None, "unit_of_measurement": "°C",
        "unit_class": None, "has_sum": False, "mean_type": StatisticMeanType.ARITHMETIC,
    }


@pytest.fixture
def history(aioclient_mock: AiohttpClientMocker):
    """Synthetic house: the last 31 days as statistics + Open-Meteo past data."""
    sim = simulate(days=DAYS + 2, seed=5)
    end = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=DAYS)
    n = DAYS * 24
    times = [start + timedelta(hours=i) for i in range(n + 48)]
    aioclient_mock.get(
        OPEN_METEO_URL,
        json={
            "hourly": {
                "time": [t.strftime("%Y-%m-%dT%H:%M") for t in times],
                "temperature_2m": [float(sim["t_out"][i]) for i in range(len(times))],
                "shortwave_radiation": [float(sim["irr"]["90_90"][i]) for i in range(len(times))],
                "global_tilted_irradiance": [float(sim["irr"]["90_90"][i]) for i in range(len(times))],
            }
        },
    )
    return sim, start, n


async def test_new_zone_learns_from_history(hass: HomeAssistant, mock_entry: MockConfigEntry, history) -> None:
    from custom_components.thermocast.core.weather import OutdoorBias

    sim, start, n = history
    sensor = sim["t_out"] + 1.5  # the outdoor sensor reads warmer than the forecast
    for sid, series in (("sensor.living", sim["air"]), ("sensor.kitchen", sim["air"]), ("sensor.outdoor", sensor)):
        rows = [{"start": start + timedelta(hours=i), "mean": float(series[i]), "min": float(series[i]),
                 "max": float(series[i])} for i in range(n)]
        async_import_statistics(hass, _meta(sid), rows)
    await async_wait_recording_done(hass)

    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    await hass.async_block_till_done(wait_background_tasks=True)

    zr = mock_entry.runtime_data.zones["zone_eg"]
    assert zr.model.n_updates > 24 * 20, zr.model.n_updates
    assert len(zr.log) == 14 * 24 and zr.params
    types = [e["type"] for e in mock_entry.runtime_data.events.to_list()]
    assert "warmstart" in types
    c = mock_entry.runtime_data
    assert c.outdoor_bias.profile() == pytest.approx([1.5] * 24, abs=0.01)
    assert c.forecast.t_out[-1] == pytest.approx(c.forecast.t_out_raw[-1] + 1.5, abs=0.01)

    # a trained zone is not re-learned on the next start, but the button re-learns on demand
    before = zr.model.n_updates
    assert await c.async_warmstart(only_fresh=True) == {}
    # an installation from before the sensor offset: trained zones, no offset yet -> learned at the next start
    c.outdoor_bias = OutdoorBias()
    assert await c.async_warmstart(only_fresh=True) == {}
    assert c.outdoor_bias.profile() == pytest.approx([1.5] * 24, abs=0.01)
    assert zr.model.n_updates == before
    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{mock_entry.entry_id}_warmstart")
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    zr = mock_entry.runtime_data.zones["zone_eg"]
    assert 0 < zr.model.n_updates <= before + 1


async def test_thermostat_hvac_action_history(hass: HomeAssistant) -> None:
    """Radiator valve signal for the warm start: the TRV's hvac_action, read from the recorder."""
    from custom_components.thermocast.history import async_fetch_attribute

    start = dt_util.utcnow() - timedelta(minutes=1)
    hass.states.async_set("climate.trv", "heat", {"hvac_action": "idle", "current_temperature": 20.0})
    await async_wait_recording_done(hass)
    hass.states.async_set("climate.trv", "heat", {"hvac_action": "idle", "current_temperature": 20.1})  # no change
    hass.states.async_set("climate.trv", "heat", {"hvac_action": "heating", "current_temperature": 20.1})
    await async_wait_recording_done(hass)
    series = await async_fetch_attribute(hass, ["climate.trv"], "hvac_action", start, dt_util.utcnow())
    assert [v for _, v in series["climate.trv"]] == ["idle", "heating"]


async def test_without_history_the_prior_stays(hass: HomeAssistant, mock_entry: MockConfigEntry, history) -> None:
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    await hass.async_block_till_done(wait_background_tasks=True)
    zr = mock_entry.runtime_data.zones["zone_eg"]
    assert zr.model.n_updates == 0
    assert "warmstart" not in [e["type"] for e in mock_entry.runtime_data.events.to_list()]
