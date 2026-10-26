"""State Duration Tracker."""
from __future__ import annotations

from homeassistant.const import CONF_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device import async_entity_id_to_device_id
from homeassistant.helpers.helper_integration import async_handle_source_entity_changes
from homeassistant.helpers.storage import Store

from .const import DOMAIN, STORAGE_VERSION
from .tracker import StateDurationConfigEntry, StateDurationTracker

PLATFORMS = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> bool:
    def set_source_entity_id_or_uuid(source_entity_id: str) -> None:
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_ENTITY_ID: source_entity_id}
        )
        hass.config_entries.async_schedule_reload(entry.entry_id)

    # Follow the source entity if it is renamed, moved to another device or removed.
    entry.async_on_unload(
        async_handle_source_entity_changes(
            hass,
            helper_config_entry_id=entry.entry_id,
            set_source_entity_id_or_uuid=set_source_entity_id_or_uuid,
            source_device_id=async_entity_id_to_device_id(hass, entry.options[CONF_ENTITY_ID]),
            source_entity_id_or_uuid=entry.options[CONF_ENTITY_ID],
        )
    )

    tracker = StateDurationTracker(hass, entry)
    await tracker.async_start()
    entry.runtime_data = tracker
    entry.async_on_unload(tracker.async_stop)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> None:
    await Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def async_migrate_entry(hass: HomeAssistant, entry: StateDurationConfigEntry) -> bool:
    """v1 kept entity/states in data; everything now lives in options."""
    if entry.version == 1:
        hass.config_entries.async_update_entry(
            entry, data={}, options={**entry.data, **entry.options}, version=2
        )
    return True
