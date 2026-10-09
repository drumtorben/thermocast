"""Better Thermostat targets per zone (opt-in): charge during a block, hold the lower bound otherwise.

The decision itself is ``core.bt.bt_decide``; this wrapper reads the thermostat, detects manual changes,
writes ``climate.set_temperature`` and keeps a small state (last write, daily count, manual override).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_BT_CONTROL, CONF_BT_ENTITY, CONF_LEADS_RELEASE, QUIET_LEAD
from .core.bt import STEP, BtInput, bt_decide

if TYPE_CHECKING:
    from .coordinator import ZoneRuntime
    from .events import EventLog

_LOGGER = logging.getLogger(__name__)
OVERRIDE_MIN = timedelta(hours=3)


@dataclass
class BtZoneState:
    last_written: float | None = None
    writes_today: int = 0
    day: date | None = None
    override_until: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_written": self.last_written,
            "writes_today": self.writes_today,
            "day": self.day.isoformat() if self.day else None,
            "override_until": self.override_until.isoformat() if self.override_until else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BtZoneState:
        day, until = data.get("day"), data.get("override_until")
        return cls(
            last_written=data.get("last_written"),
            writes_today=int(data.get("writes_today", 0)),
            day=date.fromisoformat(day) if day else None,
            override_until=datetime.fromisoformat(until) if until else None,
        )


def _current_target(hass: HomeAssistant, entity_id: str) -> float | None | str:
    """The thermostat's target, or "unavailable"."""
    st = hass.states.get(entity_id)
    if st is None or st.state in ("unavailable", "unknown"):
        return "unavailable"
    try:
        return float(st.attributes["temperature"])
    except (KeyError, TypeError, ValueError):
        return "unavailable"


class ZoneActuator:
    def __init__(self, hass: HomeAssistant, events: EventLog) -> None:
        self.hass = hass
        self.events = events
        self.state: dict[str, BtZoneState] = {}

    def to_dict(self) -> dict[str, Any]:
        return {zid: s.to_dict() for zid, s in self.state.items()}

    def load(self, data: dict[str, Any]) -> None:
        self.state = {zid: BtZoneState.from_dict(s) for zid, s in (data or {}).items()}

    def forget_writes(self) -> None:
        """Control (re)starts: whatever the thermostats show now is the starting point, not a manual override."""
        for s in self.state.values():
            s.last_written = s.override_until = None

    async def async_apply(
        self,
        zones: dict[str, ZoneRuntime],
        temps: dict[str, float | None],
        *,
        block_on: bool,
        failsafe: bool,
        enabled: bool,
        now: datetime,
        block_end: datetime | None = None,
        detect_override: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """Decide (and, when ``enabled``, write) every BT-controlled zone. Returns per zone
        ``{"target", "reason", "override_until"}`` for the panel."""
        from .coordinator import zone_base, zone_charge_target, zone_floor, zone_quiet, zone_window_open

        out: dict[str, dict[str, Any]] = {}
        today = dt_util.as_local(now).date()
        for zid, z in zones.items():
            eid = z.cfg.get(CONF_BT_ENTITY)
            if not z.cfg.get(CONF_BT_CONTROL) or not eid:
                continue
            s = self.state.setdefault(zid, BtZoneState())
            if s.day != today:
                s.day, s.writes_today = today, 0
            current = _current_target(self.hass, eid)
            if current == "unavailable":
                out[zid] = {"target": None, "reason": "unavailable", "override_until": None}
                continue
            if enabled and detect_override:
                self._detect_override(zid, z, s, current, now, block_end)
            if s.override_until is not None:  # a running manual override wins, also over a fail-safe
                if now < s.override_until:
                    out[zid] = {"target": current, "reason": "override", "override_until": s.override_until.isoformat()}
                    continue
                s.override_until, s.last_written = None, None  # override over: take the room back
            quiet_now = zone_quiet(z, now)
            inp = BtInput(
                block_on=block_on, failsafe=failsafe, floor_now=zone_floor(z, now), high=zone_charge_target(z, now),
                base=zone_base(z), quiet_now=quiet_now, quiet_soon=not quiet_now and zone_quiet(z, now, QUIET_LEAD),
                temp=temps.get(zid), leads=bool(z.cfg.get(CONF_LEADS_RELEASE, True)),
                window_open=zone_window_open(self.hass, z),
                # observe mode compares with what the thermostat has – nothing of ours was written
                last_written=s.last_written if enabled else current, writes_today=s.writes_today,
            )
            target, reason = bt_decide(inp)
            if target is not None and enabled:
                await self._write(zid, z, eid, target, now)
            elif target is not None:
                reason = "observe"
            shown = target if target is not None else (s.last_written if enabled else current)
            out[zid] = {"target": shown, "reason": reason, "override_until": None}
        return out

    def _detect_override(
        self, zid: str, z: ZoneRuntime, s: BtZoneState, current: float | None, now: datetime,
        block_end: datetime | None,
    ) -> bool:
        if s.override_until is not None or s.last_written is None or current is None:
            return False
        if abs(float(current) - s.last_written) < STEP / 2:
            return False
        s.override_until = max(now + OVERRIDE_MIN, block_end or now)
        self.events.add(now, "bt_override", zone=zid, detail=f"{current:.1f}")
        _LOGGER.info("Thermocast: %s changed by hand to %.1f – leaving it alone until %s", z.title, current,
                     s.override_until)
        return True

    async def _write(self, zid: str, z: ZoneRuntime, eid: str, target: float, now: datetime) -> None:
        s = self.state[zid]
        try:
            await self.hass.services.async_call(
                "climate", "set_temperature", {"entity_id": eid, "temperature": target}, blocking=True
            )
        except Exception as err:  # noqa: BLE001 - a broken thermostat must not stop the release logic
            _LOGGER.warning("Thermocast: setting %s to %.1f failed: %s", eid, target, err)
            return
        s.last_written = target
        s.writes_today += 1
        self.events.add(now, "bt_written", zone=zid, detail=f"{target:.1f}")

    async def async_failsafe(self, zones: dict[str, ZoneRuntime], temps: dict[str, float | None]) -> None:
        """Steuerung aus / unload: hold every controlled zone at its lower bound (quiet time respected)."""
        await self.async_apply(
            zones, temps, block_on=False, failsafe=True, enabled=True, now=dt_util.utcnow(), detect_override=False
        )
