"""Vacation period: phases at the edges, invalid periods, store round trip."""
from datetime import UTC, datetime, timedelta

from custom_components.thermocast.core.vacation import Vacation

START = datetime(2026, 11, 1, 8, tzinfo=UTC)
END = datetime(2026, 11, 8, 18, tzinfo=UTC)
H = timedelta(hours=1)


def test_phases_at_the_edges():
    v = Vacation(START, END, floor=15.0, lead_h=12)
    assert v.preheat_from() == END - 12 * H
    assert v.phase(START - timedelta(seconds=1)) is None
    assert v.phase(START) == "away"
    assert v.phase(END - 12 * H - timedelta(seconds=1)) == "away"
    assert v.phase(END - 12 * H) == "preheat"
    assert v.phase(END - timedelta(seconds=1)) == "preheat"
    assert v.phase(END) is None


def test_lead_zero_and_longer_than_the_trip():
    assert Vacation(START, END, lead_h=0).phase(END - timedelta(seconds=1)) == "away"
    short = Vacation(START, START + 6 * H, lead_h=12)
    assert short.preheat_from() == START
    assert short.phase(START) == "preheat"


def test_invalid_periods_are_inactive():
    for v in (Vacation(None, END), Vacation(START, None), Vacation(END, START), Vacation(START, START), Vacation()):
        assert not v.valid()
        assert v.phase(START + H) is None
        assert v.preheat_from() is None


def test_upcoming_or_running():
    v = Vacation(START, END)
    assert v.relevant(START - 48 * H)
    assert v.relevant(END - H)
    assert not v.relevant(END)
    assert not Vacation().relevant(START)


def test_store_round_trip_and_broken_values():
    v = Vacation(START, END, floor=14.5, lead_h=20)
    assert Vacation.from_dict(v.to_dict()) == v
    assert Vacation.from_dict(None) == Vacation()
    broken = Vacation.from_dict({"start": "kaputt", "end": END.isoformat(), "floor": None, "lead_h": "x"})
    assert broken.start is None and broken.end == END
    assert broken.floor == Vacation().floor and broken.lead_h == Vacation().lead_h
