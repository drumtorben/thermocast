"""Sensors: forecast per zone, model diagnostics, next heating block."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ThermocastConfigEntry
from .entity import HouseEntity, ZoneEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ThermocastConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([NextBlockSensor(coordinator, "next_block")])
    for sid, zone in coordinator.zones.items():
        async_add_entities(
            [
                ForecastMinSensor(coordinator, sid, zone.title, "forecast_min"),
                ModelErrorSensor(coordinator, sid, zone.title, "model_error"),
            ],
            config_subentry_id=sid,
        )


class NextBlockSensor(HouseEntity, SensorEntity):
    """The block the controller is expected to run (rollout of the panel view). The planner's single-block
    choice stays an attribute: with equal costs its start is only the earliest of several (re-planned hourly).
    Without a view (not built yet, error) the planner's choice is the state."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:fire-circle"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # the view (rollout) is built in the background after each coordinator update
        self.async_on_remove(self.coordinator.view_builder.async_add_listener(self.async_write_ha_state))

    def _rollout_block(self) -> tuple[datetime | None, datetime | None] | None:
        """(start, end) of the rollout's next block, (None, None) if it runs none, None without a view."""
        view = self.coordinator.view_builder.view
        if not view or "error" in view or "explanation" not in view:
            return None
        nb = view["explanation"].get("next_block")
        if not nb:
            return None, None
        return datetime.fromisoformat(nb["start"]), datetime.fromisoformat(nb["end"])

    def _block(self) -> tuple[datetime | None, datetime | None]:
        d = self.coordinator.data
        rollout = self._rollout_block()
        if rollout is not None:
            return rollout
        return (d.block_start, d.block_end) if d else (None, None)

    @property
    def native_value(self) -> datetime | None:
        return self._block()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = self.coordinator.data
        if d is None:
            return {}
        end = self._block()[1]
        return {
            "block_end": end.isoformat() if end else None,
            "source": "planner" if self._rollout_block() is None else "rollout",
            "planner_block_start": d.block_start.isoformat() if d.block_start else None,
            "planner_block_end": d.block_end.isoformat() if d.block_end else None,
            "forecast_age_min": round(d.forecast_age_min, 1) if d.forecast_age_min is not None else None,
            "failsafe_reason": d.failsafe_reason,
        }


class ForecastMinSensor(ZoneEntity, SensorEntity):
    """Lowest expected temperature (lower confidence bound) in comfort hours, without heating."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 1

    @property
    def native_value(self) -> float | None:
        return self.zone.min_lower if self.zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.zone
        if z is None:
            return {}
        return {
            "current_temperature": z.temp,
            "heat_demand": z.demand,
            # list for ApexCharts data_generator
            "forecast": [
                {"time": t.isoformat(), "mean": m, "lower": lo, "comfort_low": c}
                for t, m, lo, c in zip(z.times, z.mean, z.lower, z.comfort_low)
            ],
        }


class ModelErrorSensor(ZoneEntity, SensorEntity):
    """Running mean absolute one-step error of the online model (K/h)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "K/h"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:chart-bell-curve"

    @property
    def native_value(self) -> float | None:
        return round(self.zone.mae, 4) if self.zone else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.zone
        if z is None:
            return {}
        return {
            "updates": z.n_updates,
            "solar_response_K_per_h_per_kW_m2": {k: round(v, 3) for k, v in z.solar.items()},
            "parameters": {k: round(v, 6) for k, v in z.params.items()},
        }
