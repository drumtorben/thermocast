"""Ring buffer of notable events for the panel timeline (persisted in the coordinator store)."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta
from typing import Any


class EventLog:
    def __init__(self, maxlen: int = 200) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.dirty = False

    def add(
        self,
        when: datetime,
        type_: str,
        zone: str | None = None,
        detail: str | None = None,
        dedupe: timedelta | None = None,
    ) -> bool:
        """Append an event; with ``dedupe`` skip it if the same type/zone was logged within that time."""
        if dedupe is not None:
            for item in reversed(self._items):
                if item["type"] == type_ and item.get("zone") == zone:
                    if when - datetime.fromisoformat(item["time"]) < dedupe:
                        return False
                    break
        item: dict[str, Any] = {"time": when.isoformat(), "type": type_}
        if zone is not None:
            item["zone"] = zone
        if detail is not None:
            item["detail"] = detail
        self._items.append(item)
        self.dirty = True
        return True

    def to_list(self) -> list[dict[str, Any]]:
        return list(self._items)

    def load(self, items: list[dict[str, Any]]) -> None:
        self._items.clear()
        self._items.extend(items)
        self.dirty = False
