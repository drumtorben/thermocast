"""KPI tab against real recorder long-term statistics (needs the recorder fixture before `hass`)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.thermocast.const import CONF_BURNER_STARTS, CONF_HEAT_ENERGY
from custom_components.thermocast.kpi_view import async_kpis

from .test_init import _setup_entry, _setup_states

NOW = datetime(2026, 10, 4, 18, 5, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations: None) -> None:
    """Override the conftest fixture: the recorder must be set up before hass."""


def _meta(statistic_id: str, unit: str, *, mean: bool) -> dict:
    return {
        "source": "recorder",
        "statistic_id": statistic_id,
        "name": None,
        "unit_of_measurement": unit,
        "unit_class": None,
        "has_sum": not mean,
        "mean_type": StatisticMeanType.ARITHMETIC if mean else StatisticMeanType.NONE,
    }


async def _import(hass: HomeAssistant, hours: int) -> None:
    start = (NOW - timedelta(hours=hours)).replace(minute=0)
    starts, energy = [], []
    total_s = total_e = 0.0
    for i in range(hours):
        t = start + timedelta(hours=i)
        total_s += 1.0  # one burner start per hour
        total_e += 0.5
        starts.append({"start": t, "state": total_s, "sum": total_s})
        energy.append({"start": t, "state": total_e, "sum": total_e})
    async_import_statistics(hass, _meta("sensor.burner_starts", "", mean=False), starts)
    async_import_statistics(hass, _meta("sensor.energy_heating", "kWh", mean=False), energy)
    for sid, mean, low in (("sensor.outdoor", 5.0, 4.0), ("sensor.living", 20.6, 20.1), ("sensor.kitchen", 20.4, 19.9)):
        rows = [
            {"start": start + timedelta(hours=i), "mean": mean, "min": low, "max": mean + 1} for i in range(hours)
        ]
        async_import_statistics(hass, _meta(sid, "°C", mean=True), rows)
    await async_wait_recording_done(hass)


async def test_kpis_from_statistics(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo, freezer):
    freezer.move_to(NOW)
    mock_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_entry, options={CONF_BURNER_STARTS: "sensor.burner_starts", CONF_HEAT_ENERGY: "sensor.energy_heating"}
    )
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    await _import(hass, 72)

    kpis = await async_kpis(mock_entry.runtime_data, 3)
    assert kpis["missing"] == [], kpis["missing"]
    assert [d["date"] for d in kpis["days"]] == ["2026-10-02", "2026-10-03", "2026-10-04"]
    full = kpis["days"][1]  # 03.10. local: 24 hours inside the imported range
    assert full["complete"] is True
    assert full["starts"] == 24 and full["energy_kwh"] == 12.0
    assert full["t_out_mean"] == 5.0 and full["hdd"] == 10.0 and full["kwh_per_hdd"] == 1.2
    # comfort 00:00–23:59 in ZONE_DATA, 20.2 lower bound: min of both sensors' minimum
    assert full["min_leading"] == 19.9 and full["below_comfort_h"] == 0
    assert kpis["days"][2]["complete"] is False
    assert kpis["summary"]["before"] is None and kpis["summary"]["all"]["starts_per_day"] is not None


async def test_kpis_report_missing_sources(hass: HomeAssistant, mock_entry: MockConfigEntry, mock_open_meteo):
    await _setup_states(hass)
    await _setup_entry(hass, mock_entry)
    kpis = await async_kpis(mock_entry.runtime_data, 7)
    assert "burner_starts:not_configured" in kpis["missing"]
    assert "heat_energy:not_configured" in kpis["missing"]
    assert any(m.startswith("outdoor:no_statistics") for m in kpis["missing"])
    assert len(kpis["days"]) == 7 and all(d["starts"] is None for d in kpis["days"])
