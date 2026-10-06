"""Recorder access for the panel: state sequences of the last ~2 days."""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .core.series import StateSeries


async def async_fetch_states(
    hass: HomeAssistant, entity_ids: Iterable[str], start: datetime, end: datetime
) -> dict[str, StateSeries] | None:
    """State changes per entity in [start, end] incl. the state at ``start``. None if there is no recorder."""
    if "recorder" not in hass.config.components:
        return None
    ids = sorted({e for e in entity_ids if e})
    if not ids:
        return {}
    from homeassistant.components.recorder import get_instance, history

    def _query():
        return history.get_significant_states(
            hass, start, end, entity_ids=ids, significant_changes_only=False, minimal_response=True, no_attributes=True
        )

    raw = await get_instance(hass).async_add_executor_job(_query)
    return _to_series(raw)


async def async_fetch_attribute(
    hass: HomeAssistant, entity_ids: Iterable[str], attribute: str, start: datetime, end: datetime
) -> dict[str, StateSeries] | None:
    """Changes of one attribute per entity (e.g. a thermostat's ``hvac_action``). None if there is no recorder."""
    if "recorder" not in hass.config.components:
        return None
    ids = sorted({e for e in entity_ids if e})
    if not ids:
        return {}
    from homeassistant.components.recorder import get_instance, history

    def _query():
        return history.get_significant_states(
            hass, start, end, entity_ids=ids, significant_changes_only=False, minimal_response=False,
            no_attributes=False,
        )

    raw = await get_instance(hass).async_add_executor_job(_query)
    out: dict[str, StateSeries] = {}
    for eid, states in raw.items():
        series: StateSeries = []
        for st in states:
            value = str(st.attributes.get(attribute, ""))
            if not series or series[-1][1] != value:
                series.append((st.last_updated, value))
        out[eid] = series
    return out


async def async_fetch_statistics(
    hass: HomeAssistant, statistic_ids: Iterable[str], start: datetime, end: datetime, types: set[str]
) -> dict[str, dict[datetime, dict[str, float]]] | None:
    """Hourly long-term statistics per entity: {id: {UTC hour start: {"mean"/"min"/"change": value}}}.

    None if there is no recorder. Entities without state_class have no statistics (missing key).
    """
    if "recorder" not in hass.config.components:
        return None
    ids = {e for e in statistic_ids if e}
    if not ids:
        return {}
    from homeassistant.components.recorder import get_instance, statistics

    def _query():
        return statistics.statistics_during_period(hass, start, end, ids, "hour", None, types)  # type: ignore[arg-type]

    raw = await get_instance(hass).async_add_executor_job(_query)
    out: dict[str, dict[datetime, dict[str, float]]] = {}
    for eid, rows in raw.items():
        series: dict[datetime, dict[str, float]] = {}
        for row in rows:
            t = dt_util.utc_from_timestamp(row["start"])
            series[t] = {k: float(v) for k in types if (v := row.get(k)) is not None}
        out[eid] = series
    return out


def _to_series(raw) -> dict[str, StateSeries]:
    out: dict[str, StateSeries] = {}
    for eid, states in raw.items():
        series: StateSeries = []
        for st in states:
            if isinstance(st, dict):
                series.append((dt_util.parse_datetime(st["last_changed"]), st["state"]))
            else:
                series.append((st.last_changed, st.state))
        out[eid] = series
    return out
