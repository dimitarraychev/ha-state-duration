"""Sensors for State Duration Tracker."""
from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import slugify

from .tracker import StateDurationConfigEntry, StateDurationTracker
from .util import remove_stale_entities, state_label


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StateDurationConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    tracker = entry.runtime_data
    entities: list[_BaseSensor] = [
        CurrentDurationSensor(tracker, entry),
        CurrentSinceSensor(tracker, entry),
    ]
    for state in tracker.tracked:
        entities.append(PerStateDurationSensor(tracker, entry, state))
        if tracker.daily_stats:
            entities.append(TodayDurationSensor(tracker, entry, state))
            entities.append(TodayCountSensor(tracker, entry, state))

    remove_stale_entities(hass, entry, "sensor", {e.unique_id for e in entities})
    async_add_entities(entities)


class _BaseSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry, suffix: str, name: str) -> None:
        self._tracker = tracker
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"
        # Link to the source entity's device (no device_info: we must not join its device).
        self.device_entry = tracker.device_entry
        # With a device HA prefixes the device name; without one we add the title ourselves.
        self._attr_has_entity_name = tracker.device_entry is not None
        self._attr_name = name if tracker.device_entry else f"{tracker.title} {name}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._tracker.async_add_listener(self.async_write_ha_state))


class _DurationSensor(_BaseSensor):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_suggested_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0
    _attr_icon = "mdi:timer-outline"


class CurrentDurationSensor(_DurationSensor):
    """How long the entity has been in its current tracked state."""

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry) -> None:
        super().__init__(tracker, entry, "current_duration", "Current state duration")

    @property
    def native_value(self) -> int | None:
        return self._tracker.duration

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        t = self._tracker
        return {
            "source_entity": t.entity_id,
            "tracked_state": t.state,
            "tracked_state_label": state_label(t.hass, t.entity_id, t.state) if t.state else None,
            "since": t.since.isoformat() if t.since else None,
        }


class CurrentSinceSensor(_BaseSensor):
    """When the entity entered its current tracked state."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry) -> None:
        super().__init__(tracker, entry, "current_since", "Current state since")

    @property
    def native_value(self):
        return self._tracker.since


class PerStateDurationSensor(_DurationSensor):
    """Time spent in one specific state (0 while the entity is in another state)."""

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry, state: str) -> None:
        label = state_label(tracker.hass, tracker.entity_id, state)
        super().__init__(tracker, entry, f"state_{slugify(state)}", f"{label} duration")
        self._state = state

    @property
    def native_value(self) -> int:
        if self._tracker.state == self._state:
            return self._tracker.duration or 0
        return 0


class TodayDurationSensor(_DurationSensor):
    """Total time spent in one state since local midnight."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:timer-sand"

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry, state: str) -> None:
        label = state_label(tracker.hass, tracker.entity_id, state)
        super().__init__(tracker, entry, f"today_{slugify(state)}", f"{label} today")
        self._state = state

    @property
    def native_value(self) -> int:
        return self._tracker.today_seconds(self._state)


class TodayCountSensor(_BaseSensor):
    """How many times a state was entered since local midnight."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:counter"

    def __init__(self, tracker: StateDurationTracker, entry: StateDurationConfigEntry, state: str) -> None:
        label = state_label(tracker.hass, tracker.entity_id, state)
        super().__init__(tracker, entry, f"count_{slugify(state)}", f"{label} count today")
        self._state = state

    @property
    def native_value(self) -> int:
        return self._tracker.count_today(self._state)
