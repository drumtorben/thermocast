"""Daily KPI aggregation: local days (DST), counter resets, degree days, comfort, before/after."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from . import core_helpers  # noqa: F401
from core.kpi import ZoneKpiInput, aggregate, daily_kpis, summary

TZ = ZoneInfo("Europe/Berlin")


def _hours(day: date) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=TZ).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=TZ).astimezone(UTC)
    out, t = [], start
    while t < end:
        out.append(t)
        t += timedelta(hours=1)
    return out


def test_dst_day_has_25_hours_and_counts_once():
    d = date(2026, 10, 25)
    hours = _hours(d)
    assert len(hours) == 25
    rows = daily_kpis(
        days=[d], tz=TZ, today=date(2026, 10, 26),
        starts={h: 1.0 for h in hours}, energy={h: 0.5 for h in hours},
        t_out={h: 5.0 for h in hours}, zones=[],
    )
    r = rows[0]
    assert r["starts"] == 25 and r["energy_kwh"] == 12.5
    assert r["t_out_mean"] == 5.0 and r["hdd"] == 10.0 and r["kwh_per_hdd"] == 1.25
    assert r["complete"] is True


def test_counter_reset_and_missing_data():
    d = date(2026, 10, 3)
    hours = _hours(d)
    starts = {hours[0]: 3.0, hours[1]: -12000.0, hours[2]: 2.0}
    rows = daily_kpis(days=[d, d + timedelta(days=1)], tz=TZ, today=d, starts=starts, energy=None,
                      t_out={h: 5.0 for h in hours[:10]}, zones=[])
    assert rows[0]["starts"] == 5.0
    assert rows[0]["energy_kwh"] is None and rows[0]["t_out_mean"] is None and rows[0]["hdd"] is None
    assert rows[0]["complete"] is False  # today
    assert rows[1]["starts"] is None


def test_mild_day_has_no_kwh_per_hdd():
    d = date(2026, 6, 1)
    hours = _hours(d)
    rows = daily_kpis(days=[d], tz=TZ, today=date(2026, 6, 2), starts=None, energy={h: 0.1 for h in hours},
                      t_out={h: 14.5 for h in hours}, zones=[])
    assert rows[0]["hdd"] == 0.5 and rows[0]["kwh_per_hdd"] is None


def test_comfort_min_and_below_hours():
    d = date(2026, 10, 3)
    hours = _hours(d)
    comfort = {h: (20.0 if 6 <= h.astimezone(TZ).hour < 22 else None) for h in hours}
    zone = ZoneKpiInput(
        hourly_min={h: 19.5 if h.astimezone(TZ).hour == 7 else 20.3 for h in hours},
        hourly_mean={h: 19.8 if h.astimezone(TZ).hour in (7, 8) else 20.5 for h in hours},
        comfort=comfort,
    )
    rows = daily_kpis(days=[d], tz=TZ, today=date(2026, 10, 4), starts=None, energy=None, t_out=None, zones=[zone])
    assert rows[0]["min_leading"] == 19.5 and rows[0]["below_comfort_h"] == 2


def test_summary_before_after():
    rows = [
        {"date": f"2026-10-0{i}", "complete": True, "starts": s, "energy_kwh": 20.0, "t_out_mean": 5.0, "hdd": 10.0,
         "kwh_per_hdd": 2.0, "min_leading": 20.0 + i / 10, "below_comfort_h": 0}
        for i, s in ((1, 30.0), (2, 34.0), (3, 8.0), (4, 6.0))
    ]
    rows.append({**rows[-1], "date": "2026-10-05", "complete": False, "starts": 99.0})
    s = summary(rows, control_since=date(2026, 10, 3))
    assert s["all"]["n_days"] == 4 and s["all"]["starts_per_day"] == pytest.approx(19.5)
    assert s["before"]["starts_per_day"] == 32.0 and s["after"]["starts_per_day"] == 7.0
    assert s["after"]["kwh_per_hdd"] == 2.0 and s["before"]["min_leading"] == 20.1
    assert summary(rows, None)["before"] is None
    assert aggregate([]) is None
