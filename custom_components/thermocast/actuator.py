"""Switches the heat source release – conservatively.

Rules (see core/rules.py):
* Turning heating ON is always allowed (fail-safe direction), except during the minimum pause.
* Turning heating OFF respects a minimum block length and a daily switch budget.

EEPROM protection (the RC310 stores the release value in EEPROM):
* Nothing is written if the entity already shows the requested state, or while it is unavailable.
* Every write must be *confirmed* by the entity state. An unconfirmed write is retried only after
  10 min, then with exponential backoff (30 min … 6 h); each failure is logged and raises a repair issue.
* Hard cap of ``MAX_WRITES_PER_DAY`` writes per local day, whatever happens.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MAX_SWITCHES,
    CONF_MIN_BLOCK_H,
    CONF_MIN_PAUSE_H,
    CONF_RELEASE_ENTITY,
    CONF_RELEASE_OFF,
    CONF_RELEASE_ON,
    DEFAULT_MAX_SWITCHES,
    DEFAULT_MIN_BLOCK_H,
    DEFAULT_MIN_PAUSE_H,
    DOMAIN,
)
from .core.rules import Rules, apply_rules, release_state

_LOGGER = logging.getLogger(__name__)

VERIFY_AFTER = timedelta(minutes=10)  # time the entity gets to reflect a write (EMS-ESP via MQTT)
BACKOFF_START = timedelta(minutes=30)
BACKOFF_MAX = timedelta(hours=6)
MAX_WRITES_PER_DAY = 40  # incl. retries; 40/day ≈ 15 000/year, far below typical EEPROM endurance
ISSUE_UNCONFIRMED = "release_not_confirmed"
ISSUE_BUDGET = "release_write_budget"


class Actuator:
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.commanded: bool | None = None
        self.last_change: datetime | None = None
        self.switches_today = 0
        self.day: date | None = None
        self.last_decision: bool | None = None
        # write verification
        self.last_write_target: bool | None = None  # pending (unconfirmed) write
        self.last_write_at: datetime | None = None
        self.write_failures = 0
        self.writes_today = 0
        self._budget_reported = False
        # hooks for the event log
        self.on_write: Callable[[bool], None] | None = None
        self.on_event: Callable[[str, str | None], None] | None = None

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "commanded": self.commanded,
            "last_change": self.last_change.isoformat() if self.last_change else None,
            "switches_today": self.switches_today,
            "day": self.day.isoformat() if self.day else None,
            "last_write_target": self.last_write_target,
            "last_write_at": self.last_write_at.isoformat() if self.last_write_at else None,
            "write_failures": self.write_failures,
            "writes_today": self.writes_today,
        }

    def load(self, data: dict[str, Any]) -> None:
        self.commanded = data.get("commanded")
        lc = data.get("last_change")
        self.last_change = datetime.fromisoformat(lc) if lc else None
        self.switches_today = int(data.get("switches_today", 0))
        d = data.get("day")
        self.day = date.fromisoformat(d) if d else None
        self.last_write_target = data.get("last_write_target")
        lw = data.get("last_write_at")
        self.last_write_at = datetime.fromisoformat(lw) if lw else None
        self.write_failures = int(data.get("write_failures", 0))
        self.writes_today = int(data.get("writes_today", 0))

    # ----------------------------------------------------------------- config
    def _opt(self, key: str, default: float) -> float:
        return float(self.entry.options.get(key, default))

    @property
    def entity_id(self) -> str:
        return self.entry.data[CONF_RELEASE_ENTITY]

    @property
    def rules(self) -> Rules:
        return Rules(
            min_block_h=self._opt(CONF_MIN_BLOCK_H, DEFAULT_MIN_BLOCK_H),
            min_pause_h=self._opt(CONF_MIN_PAUSE_H, DEFAULT_MIN_PAUSE_H),
            max_switches=int(self._opt(CONF_MAX_SWITCHES, DEFAULT_MAX_SWITCHES)),
        )

    def elapsed_h(self, now: datetime) -> float:
        return (now - self.last_change).total_seconds() / 3600 if self.last_change else math.inf

    def entity_is_on(self) -> bool | None:
        state = self.hass.states.get(self.entity_id)
        return release_state(
            state.state if state else None,
            self.entity_id.split(".")[0],
            self.entry.data.get(CONF_RELEASE_ON),
            self.entry.data.get(CONF_RELEASE_OFF),
        )

    # ------------------------------------------------------------ bookkeeping
    def _emit(self, type_: str, detail: str | None = None) -> None:
        if self.on_event is not None:
            self.on_event(type_, detail)

    def _new_day(self, now: datetime) -> None:
        today = dt_util.as_local(now).date()
        if self.day != today:
            self.day, self.switches_today, self.writes_today = today, 0, 0
            if self._budget_reported:
                self._budget_reported = False
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_BUDGET)

    def _check_confirmation(self, current: bool | None) -> None:
        """A pending write is confirmed once the entity shows the written state."""
        if self.last_write_target is not None and current == self.last_write_target:
            self.last_write_target = self.last_write_at = None
            if self.write_failures:
                self.write_failures = 0
                ir.async_delete_issue(self.hass, DOMAIN, ISSUE_UNCONFIRMED)

    def _retry_after(self) -> timedelta:
        if self.write_failures == 0:
            return VERIFY_AFTER
        return min(BACKOFF_START * 2 ** (self.write_failures - 1), BACKOFF_MAX)

    def _issue(self, issue_id: str) -> None:
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id, is_fixable=False, severity=ir.IssueSeverity.WARNING,
            translation_key=issue_id, translation_placeholders={"entity": self.entity_id},
        )

    async def _try_write(self, target: bool, now: datetime) -> bool:
        """Write unless that would be pointless (unavailable), too early (unconfirmed) or over budget."""
        state = self.hass.states.get(self.entity_id)
        if state is None or state.state == "unavailable":
            return False
        if self.last_write_target == target and self.last_write_at is not None:
            if now - self.last_write_at < self._retry_after():
                return False  # still waiting for the entity to confirm the previous write
            self.write_failures += 1
            _LOGGER.warning(
                "Thermocast: %s did not confirm the release %s (attempt %s)",
                self.entity_id, "ON" if target else "OFF", self.write_failures,
            )
            self._emit("release_unconfirmed", f"{'on' if target else 'off'} #{self.write_failures}")
            self._issue(ISSUE_UNCONFIRMED)
        if self.writes_today >= MAX_WRITES_PER_DAY:
            if not self._budget_reported:
                self._budget_reported = True
                _LOGGER.warning("Thermocast: write budget of %s per day used up", MAX_WRITES_PER_DAY)
                self._emit("write_budget", str(MAX_WRITES_PER_DAY))
                self._issue(ISSUE_BUDGET)
            return False
        await self._write(target)
        self.last_write_target, self.last_write_at = target, now
        self.writes_today += 1
        return True

    # ------------------------------------------------------------------ logic
    async def async_apply(self, want_on: bool, enabled: bool, now: datetime) -> tuple[bool, str | None]:
        """Apply the planner decision. Returns (intended release state, reason it differs from the planner)."""
        self.last_decision = want_on
        if not enabled:
            return want_on, "observe"  # observe mode: never touch the heat source

        self._new_day(now)
        known = self.entity_is_on()
        self._check_confirmation(known)
        current = known if known is not None else (self.commanded if self.commanded is not None else True)
        target, reason = apply_rules(want_on, current, self.elapsed_h(now), self.switches_today, self.rules)
        if target != current:
            if self.commanded != target:  # a new switching decision (not a retry)
                self.last_change = now
                self.switches_today += 1
            await self._try_write(target, now)
        self.commanded = target
        return target, reason

    async def async_force_on(self, now: datetime | None = None) -> None:
        """Fail-safe: allow heating (used on errors, unload, observe-mode toggle)."""
        now = now or dt_util.utcnow()
        self._new_day(now)
        known = self.entity_is_on()
        self._check_confirmation(known)
        if known is not True:
            await self._try_write(True, now)
        self.commanded = True

    async def _write(self, on: bool) -> None:
        domain = self.entity_id.split(".")[0]
        _LOGGER.info("Thermocast: heating release -> %s (%s)", "ON" if on else "OFF", self.entity_id)
        if self.on_write is not None:
            self.on_write(on)
        if domain in ("select", "input_select"):
            option = self.entry.data[CONF_RELEASE_ON if on else CONF_RELEASE_OFF]
            await self.hass.services.async_call(
                domain, "select_option", {"entity_id": self.entity_id, "option": option}, blocking=True
            )
        elif domain in ("number", "input_number"):
            value = float(self.entry.data[CONF_RELEASE_ON if on else CONF_RELEASE_OFF])
            await self.hass.services.async_call(
                domain, "set_value", {"entity_id": self.entity_id, "value": value}, blocking=True
            )
        else:
            await self.hass.services.async_call(
                domain, "turn_on" if on else "turn_off", {"entity_id": self.entity_id}, blocking=True
            )
