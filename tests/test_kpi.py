"""Daily KPI aggregation: local days (DST), counter resets, degree days, comfort, before/after."""
# ruff: noqa: I001
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from . import core_helpers  # noqa: F401
from core.kpi import ZoneKpiInput, aggregate, daily_kpis, degree_day_fit, summary

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
    assert r["t_out_mean"] == 5.0 and r["hdd"] == 11.0 and r["kwh_per_hdd"] == pytest.approx(1.14)
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
                      t_out={h: 15.5 for h in hours}, zones=[])
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


def _day(i: int, hdd: float | None, kwh: float | None, complete: bool = True) -> dict:
    return {"date": f"2026-11-{i:02d}", "complete": complete, "starts": 5.0, "energy_kwh": kwh, "t_out_mean": None,
            "hdd": hdd, "kwh_per_hdd": None, "min_leading": None, "below_comfort_h": None}


def test_degree_day_fit_recovers_line():
    """kWh/day = a + b·HDD over heating days; mild days (HDD < 1), incomplete days and gaps stay out."""
    rows = [_day(i, h, 4.0 + 5.5 * h) for i, h in enumerate((2.0, 4.0, 6.0, 8.0, 10.0), start=1)]
    rows += [_day(6, 0.5, 30.0), _day(7, 9.0, 999.0, complete=False), _day(8, None, 20.0), _day(9, 5.0, None)]
    fit = degree_day_fit(rows)
    assert fit["slope"] == pytest.approx(5.5) and fit["intercept"] == pytest.approx(4.0)
    assert fit["n"] == 5 and fit["r2"] == pytest.approx(1.0)
    assert aggregate(rows)["fit"] == fit


def test_degree_day_fit_needs_spread_and_days():
    assert degree_day_fit([_day(i, 4.0 + i / 10, 30.0) for i in range(1, 8)]) is None  # HDD range < 2 K·d
    assert degree_day_fit([_day(i, h, 5.0 * h) for i, h in enumerate((2.0, 5.0, 8.0), start=1)]) is None  # < 4 days
