"""Core tracking logic: which tracked state an entity is in, and since when."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import CONF_ENTITY_ID
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ALERT_MINUTES,
    CONF_ALERT_STATE,
    CONF_DAILY_STATS,
    CONF_STATES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    IGNORED_STATES,
    STORAGE_VERSION,
)


class StateDurationTracker:
    """Tracks the time an entity has spent in its current (tracked) state."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, subentry: ConfigSubentry) -> None:
        self.hass = hass
        opts = subentry.data
        self.subentry_id: str = subentry.subentry_id
        self.title: str = subentry.title
        self.entity_id: str = er.async_validate_entity_id(er.async_get(hass), opts[CONF_ENTITY_ID])
        self.tracked: list[str] = list(opts[CONF_STATES])
        self.daily_stats: bool = bool(opts.get(CONF_DAILY_STATS, False))
        self.interval = timedelta(minutes=int(opts.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)))
        self.alert_state: str | None = opts.get(CONF_ALERT_STATE) or None
        self.alert_seconds: int = int(opts.get(CONF_ALERT_MINUTES) or 0) * 60
        # Entities link to the source device; we never add our config entry to it.
        self.device_entry = async_entity_id_to_device(hass, self.entity_id)

        self.state: str | None = None
        self.since: datetime | None = None
        self._day: str | None = None
        self._totals: dict[str, float] = {}
        self._counts: dict[str, int] = {}

        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{subentry.subentry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._discarded = False

    # -- lifecycle -----------------------------------------------------
    async def async_start(self) -> None:
        data = await self._store.async_load()
        if data:
            self.state = data.get("state")
            since = data.get("since")
            self.since = dt_util.parse_datetime(since) if since else None
            self._day = data.get("day")
            self._totals = {k: float(v) for k, v in (data.get("totals") or {}).items()}
            self._counts = {k: int(v) for k, v in (data.get("counts") or {}).items()}
        if self.since is None or self.state not in self.tracked:
            self.state = None
            self.since = None

        current = self.hass.states.get(self.entity_id)
        if current is not None:
            now = dt_util.utcnow()
            if data:
                self._apply(current.state, now)
            else:
                # First ever run: best guess is the entity's own last_changed.
                self._apply(current.state, min(current.last_changed, now), count=False)

        self._unsubs += [
            async_track_state_change_event(self.hass, [self.entity_id], self._async_state_event),
            async_track_time_interval(self.hass, self._async_tick, self.interval),
            async_track_time_change(self.hass, self._async_tick, hour=0, minute=0, second=1),
        ]
        self._schedule_save()

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if not self._discarded:
            await self._store.async_save(self._data())

    async def async_discard(self) -> None:
        """The tracker was deleted: forget its stored data."""
        self._discarded = True
        await self._store.async_remove()

    # -- core logic ----------------------------------------------------
    def _apply(self, new_state: str, when: datetime, *, count: bool = True) -> bool:
        """Apply a source state. Returns True if our tracked state changed."""
        if new_state in IGNORED_STATES:
            return False  # never reset, never start
        if new_state not in self.tracked:
            if self.state is None:
                return False
            self._fold(when)
            self.state = None
            self.since = None
            return True
        if new_state == self.state:
            return False  # e.g. open -> unavailable -> open keeps the timer
        self._fold(when)
        self.state = new_state
        self.since = when
        if count:
            self._counts[new_state] = self._counts.get(new_state, 0) + 1
        return True

    def _roll_day(self, now: datetime) -> None:
        today = dt_util.as_local(now).date().isoformat()
        if self._day != today:
            self._day = today
            self._totals = {}
            self._counts = {}

    def _fold(self, when: datetime) -> None:
        """Add the finished run (clipped to today) to the daily total."""
        self._roll_day(when)
        if self.state is None or self.since is None:
            return
        start = max(self.since, dt_util.start_of_local_day(when))
        if when > start:
            self._totals[self.state] = self._totals.get(self.state, 0.0) + (when - start).total_seconds()

    @callback
    def _async_state_event(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        if self._apply(new.state, dt_util.utcnow()):
            self._schedule_save()
            self._notify()

    @callback
    def _async_tick(self, _now: datetime) -> None:
        self._notify()
        self._schedule_save()

    # -- values --------------------------------------------------------
    @property
    def duration(self) -> int | None:
        """Seconds in the current tracked state, or None if untracked."""
        if self.since is None:
            return None
        return max(0, int((dt_util.utcnow() - self.since).total_seconds()))

    def today_seconds(self, state: str) -> int:
        """Total seconds spent in `state` since local midnight."""
        now = dt_util.utcnow()
        self._roll_day(now)
        total = self._totals.get(state, 0.0)
        if self.state == state and self.since is not None:
            start = max(self.since, dt_util.start_of_local_day(now))
            total += max(0.0, (now - start).total_seconds())
        return int(total)

    def count_today(self, state: str) -> int:
        """Times `state` was entered since local midnight."""
        self._roll_day(dt_util.utcnow())
        return self._counts.get(state, 0)

    @property
    def alert_active(self) -> bool:
        if not self.alert_state or not self.alert_seconds:
            return False
        return self.state == self.alert_state and (self.duration or 0) >= self.alert_seconds

    # -- persistence & listeners ----------------------------------------
    def _data(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "since": self.since.isoformat() if self.since else None,
            "day": self._day,
            "totals": self._totals,
            "counts": self._counts,
        }

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data, 1)

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        @callback
        def remove() -> None:
            self._listeners.remove(cb)

        return remove

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()


type StateDurationConfigEntry = ConfigEntry[dict[str, StateDurationTracker]]
