"""State Duration Tracker: one service entry, one config subentry per tracked entity."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigSubentry
from homeassistant.const import CONF_ENTITY_ID, Platform
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, valid_entity_id
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_entity_registry_updated_event
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION, SUBENTRY_TYPE
from .tracker import StateDurationConfigEntry, StateDurationTracker

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> bool:
    trackers: dict[str, StateDurationTracker] = {}
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_TYPE:
            continue
        try:
            tracker = StateDurationTracker(hass, entry, subentry)
        except Exception:  # noqa: BLE001 - one broken tracker must not stop the others
            _LOGGER.exception("Could not set up tracker %s", subentry.title)
            continue
        await tracker.async_start()
        entry.async_on_unload(tracker.async_stop)
        entry.async_on_unload(_async_follow_source(hass, entry, subentry, tracker))
        trackers[subentry.subentry_id] = tracker

    entry.runtime_data = trackers
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


def _async_follow_source(
    hass: HomeAssistant,
    entry: StateDurationConfigEntry,
    subentry: ConfigSubentry,
    tracker: StateDurationTracker,
) -> CALLBACK_TYPE:
    """Follow renames, device moves and removal of ONE tracker's source entity.

    Core's async_handle_source_entity_changes acts on every entity of a config entry,
    which would be wrong here because many trackers share one entry.
    """
    registry = er.async_get(hass)
    source_device_id = er.async_get(hass).async_get(tracker.entity_id)
    source_device_id = source_device_id.device_id if source_device_id else None

    def own_entities() -> list[er.RegistryEntry]:
        return [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.config_subentry_id == subentry.subentry_id
        ]

    async def updated(event: Event[er.EventEntityRegistryUpdatedData]) -> None:
        nonlocal source_device_id
        data = event.data
        if data["action"] == "remove":
            for e in own_entities():
                registry.async_update_entity(e.entity_id, device_id=None)
            return
        if data["action"] != "update":
            return
        changes = data["changes"]
        if "entity_id" in changes and valid_entity_id(subentry.data[CONF_ENTITY_ID]):
            # The update listener reloads the entry with the new id.
            hass.config_entries.async_update_subentry(
                entry, subentry, data={**subentry.data, CONF_ENTITY_ID: data["entity_id"]}
            )
            return
        if "device_id" in changes:
            new = registry.async_get(data["entity_id"])
            new_device_id = new.device_id if new else None
            if new_device_id != source_device_id:
                source_device_id = new_device_id
                for e in own_entities():
                    registry.async_update_entity(e.entity_id, device_id=new_device_id)
                hass.config_entries.async_schedule_reload(entry.entry_id)

    return async_track_entity_registry_updated_event(hass, tracker.entity_id, updated)


async def _async_update_listener(hass: HomeAssistant, entry: StateDurationConfigEntry) -> None:
    """A tracker was added, changed or removed: forget deleted ones and reload."""
    for subentry_id, tracker in entry.runtime_data.items():
        if subentry_id not in entry.subentries:
            await tracker.async_discard()
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> None:
    for subentry_id in entry.subentries:
        await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{subentry_id}").async_remove()
