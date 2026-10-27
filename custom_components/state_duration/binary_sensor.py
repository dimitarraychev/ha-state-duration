"""Optional 'state lasted too long' binary sensor."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .tracker import StateDurationConfigEntry, StateDurationTracker
from .util import remove_stale_entities, state_label


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StateDurationConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    for subentry_id, tracker in entry.runtime_data.items():
        entities = []
        if tracker.alert_state and tracker.alert_seconds:
            entities.append(TooLongBinarySensor(tracker))
        remove_stale_entities(hass, entry, subentry_id, "binary_sensor", {e.unique_id for e in entities})
        async_add_entities(entities, config_subentry_id=subentry_id)


class TooLongBinarySensor(BinarySensorEntity):
    """On while the entity has been in the alert state for at least N minutes."""

    _attr_should_poll = False
    _attr_icon = "mdi:timer-alert-outline"

    def __init__(self, tracker: StateDurationTracker) -> None:
        self._tracker = tracker
        label = state_label(tracker.hass, tracker.entity_id, tracker.alert_state or "")
        self._attr_unique_id = f"{tracker.subentry_id}_alert"
        self.device_entry = tracker.device_entry
        self._attr_has_entity_name = tracker.device_entry is not None
        name = f"{label} too long"
        self._attr_name = name if tracker.device_entry else f"{tracker.title} {name}"
        self._key: tuple[str | None, datetime | None] | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def is_on(self) -> bool:
        return self._tracker.alert_active

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "alert_state": self._tracker.alert_state,
            "threshold_minutes": self._tracker.alert_seconds // 60,
        }

    async def async_added_to_hass(self) -> None:
        self._reschedule()
        self.async_on_remove(self._tracker.async_add_listener(self._on_tracker_change))
        self.async_on_remove(self._cancel)

    @callback
    def _cancel(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _reschedule(self) -> None:
        """(Re)arm a timer for the exact moment the threshold is crossed."""
        t = self._tracker
        key = (t.state, t.since)
        if key == self._key:
            return
        self._key = key
        self._cancel()
        if t.state == t.alert_state and t.since is not None:
            target = t.since + timedelta(seconds=t.alert_seconds)
            if target > dt_util.utcnow():
                self._unsub = async_track_point_in_utc_time(self.hass, self._fire, target)

    @callback
    def _fire(self, _now: datetime) -> None:
        self._unsub = None
        self.async_write_ha_state()

    @callback
    def _on_tracker_change(self) -> None:
        self._reschedule()
        self.async_write_ha_state()
