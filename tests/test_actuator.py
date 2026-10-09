"""Actuator EEPROM protection: write verification, backoff, daily cap, unavailable entity."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

from custom_components.thermocast.actuator import ISSUE_BUDGET, ISSUE_UNCONFIRMED, MAX_WRITES_PER_DAY, Actuator
from custom_components.thermocast.const import CONF_RELEASE_ENTITY, CONF_RELEASE_OFF, CONF_RELEASE_ON, DOMAIN

T0 = datetime(2026, 10, 4, 10, 0, tzinfo=UTC)


def _actuator(hass: HomeAssistant, options: dict | None = None) -> tuple[Actuator, list]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_RELEASE_ENTITY: "number.summer_threshold", CONF_RELEASE_ON: "16", CONF_RELEASE_OFF: "10"},
        options=options or {"min_block_hours": 0, "min_pause_hours": 0, "max_switches_per_day": 999},
    )
    act = Actuator(hass, entry)
    events: list = []
    act.on_event = lambda t, d: events.append((t, d))
    return act, events


async def test_stuck_entity_backs_off_and_raises_issue(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "number", "set_value")  # the value never "sticks"
    hass.states.async_set("number.summer_threshold", "16")
    act, events = _actuator(hass)

    await act.async_apply(False, True, T0)  # want OFF -> first write
    assert len(calls) == 1
    for minutes in (5, 9):  # waiting for confirmation: no rewrite
        await act.async_apply(False, True, T0 + timedelta(minutes=minutes))
    assert len(calls) == 1 and act.switches_today == 1

    await act.async_apply(False, True, T0 + timedelta(minutes=11))  # unconfirmed -> retry #1
    assert len(calls) == 2 and act.write_failures == 1 and act.switches_today == 1
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_UNCONFIRMED) is not None
    assert events[-1][0] == "release_unconfirmed"

    await act.async_apply(False, True, T0 + timedelta(minutes=30))  # backoff 30 min after retry #1
    assert len(calls) == 2
    await act.async_apply(False, True, T0 + timedelta(minutes=42))
    assert len(calls) == 3 and act.write_failures == 2  # next wait: 60 min
    await act.async_apply(False, True, T0 + timedelta(minutes=95))
    assert len(calls) == 3

    # the value finally arrives -> confirmed, issue gone, no further writes
    hass.states.async_set("number.summer_threshold", "10")
    await act.async_apply(False, True, T0 + timedelta(minutes=96))
    assert act.write_failures == 0 and act.last_write_target is None
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_UNCONFIRMED) is None
    await act.async_apply(False, True, T0 + timedelta(hours=5))
    assert len(calls) == 3


async def test_unavailable_entity_is_never_written(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "number", "set_value")
    hass.states.async_set("number.summer_threshold", "unavailable")
    act, _ = _actuator(hass)
    await act.async_force_on(T0)
    await act.async_apply(False, True, T0 + timedelta(minutes=15))
    assert calls == []


async def test_daily_write_cap(hass: HomeAssistant) -> None:
    calls = async_mock_service(hass, "number", "set_value")

    async def flip(call):  # entity follows every write -> every write is confirmed
        hass.states.async_set("number.summer_threshold", str(int(call.data["value"])))

    hass.services.async_register("number", "set_value", flip)
    hass.states.async_set("number.summer_threshold", "16")
    act, events = _actuator(hass)
    t = T0
    for i in range(MAX_WRITES_PER_DAY + 5):
        await act.async_apply(i % 2 == 1, True, t)
        t += timedelta(minutes=1)
    assert act.writes_today == MAX_WRITES_PER_DAY
    assert ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_BUDGET) is not None
    assert [e for e in events if e[0] == "write_budget"] == [("write_budget", str(MAX_WRITES_PER_DAY))]
    # next local day: budget and issue reset
    await act.async_apply(True, True, T0 + timedelta(days=1))
    assert act.writes_today <= 1 and ir.async_get(hass).async_get_issue(DOMAIN, ISSUE_BUDGET) is None
    assert calls == []  # the mock was replaced by `flip`


async def test_state_roundtrip(hass: HomeAssistant) -> None:
    async_mock_service(hass, "number", "set_value")
    hass.states.async_set("number.summer_threshold", "16")
    act, _ = _actuator(hass)
    await act.async_apply(False, True, T0)
    other, _ = _actuator(hass)
    other.load(act.to_dict())
    assert other.to_dict() == act.to_dict() and other.last_write_target is False


async def test_failsafe_overrides_pause_and_owes_no_block(hass: HomeAssistant) -> None:
    async def follow(call):  # the entity takes every written value
        hass.states.async_set("number.summer_threshold", str(int(call.data["value"])))

    hass.services.async_register("number", "set_value", follow)
    hass.states.async_set("number.summer_threshold", "16")
    act, _ = _actuator(hass, {"min_block_hours": 3, "min_pause_hours": 2, "max_switches_per_day": 12})
    await act.async_apply(False, True, T0)  # OFF -> minimum pause of 2 h starts
    assert hass.states.get("number.summer_threshold").state == "10"

    # fail-safe 30 min later: heating allowed at once, despite the minimum pause
    on, reason = await act.async_apply(True, True, T0 + timedelta(minutes=30), forced=True)
    assert (on, reason) == (True, "failsafe") and hass.states.get("number.summer_threshold").state == "16"

    # fail-safe over, planner does not want heat: no 3 h minimum block is owed
    on, reason = await act.async_apply(False, True, T0 + timedelta(minutes=45))
    assert (on, reason) == (False, None) and hass.states.get("number.summer_threshold").state == "10"


async def test_block_is_bridged_when_the_next_one_falls_into_the_pause(hass: HomeAssistant) -> None:
    async def follow(call):
        hass.states.async_set("number.summer_threshold", str(int(call.data["value"])))

    hass.services.async_register("number", "set_value", follow)
    hass.states.async_set("number.summer_threshold", "16")
    act, _ = _actuator(hass, {"min_block_hours": 1, "min_pause_hours": 2, "max_switches_per_day": 12})
    await act.async_apply(True, True, T0 - timedelta(hours=3))
    # planner pauses, but plans the next block in 1.8 h – the minimum pause would delay it: stay on
    on, reason = await act.async_apply(False, True, T0, next_on_h=1.8)
    assert (on, reason) == (True, "bridge") and hass.states.get("number.summer_threshold").state == "16"
    assert act.switches_today == 0
    # a real pause (next block after the minimum pause) still ends the block
    on, reason = await act.async_apply(False, True, T0 + timedelta(minutes=15), next_on_h=3.0)
    assert (on, reason) == (False, None) and hass.states.get("number.summer_threshold").state == "10"


async def test_reconfigured_entity_drops_pending_write(hass: HomeAssistant) -> None:
    async_mock_service(hass, "number", "set_value")
    hass.states.async_set("number.summer_threshold", "16")
    act, _ = _actuator(hass)
    await act.async_apply(False, True, T0)
    stored = {**act.to_dict(), "entity": "select.season", "write_failures": 3}  # written for the old entity
    other, _ = _actuator(hass)
    other.load(stored)
    assert other.last_write_target is None and other.last_write_at is None and other.write_failures == 0
    assert other.commanded is False and other.writes_today == 1  # switching state + EEPROM budget stay
