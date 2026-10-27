"""Helpers for State Duration Tracker."""
from __future__ import annotations

from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import BINARY_SENSOR_LABELS, DOMAIN_STATES, IGNORED_STATES


def state_label(hass: HomeAssistant, entity_id: str, state: str) -> str:
    """Return a human friendly label for a raw state."""
    domain = entity_id.split(".", 1)[0]
    if domain == "binary_sensor" and state in ("on", "off"):
        ent = hass.states.get(entity_id)
        device_class = ent.attributes.get("device_class") if ent else None
        if device_class in BINARY_SENSOR_LABELS:
            on_label, off_label = BINARY_SENSOR_LABELS[device_class]
            return on_label if state == "on" else off_label
    return state.replace("_", " ").capitalize()


def option_label(hass: HomeAssistant, entity_id: str, state: str) -> str:
    """Label for the selector, showing the raw state when it differs."""
    label = state_label(hass, entity_id, state)
    if label.lower().replace(" ", "_") == state:
        return label
    return f"{label} ({state})"


def suggest_states(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Suggest states for an entity, based on its attributes and domain."""
    domain = entity_id.split(".", 1)[0]
    state = hass.states.get(entity_id)
    found: list[str] = []
    if state is not None:
        for attr in ("options", "hvac_modes"):
            value = state.attributes.get(attr)
            if isinstance(value, (list, tuple)):
                found.extend(str(v) for v in value)
    if not found:
        found.extend(DOMAIN_STATES.get(domain, ()))
    if state is not None:
        found.append(state.state)
    return clean_states(found)


def clean_states(states: Iterable[str]) -> list[str]:
    """Strip, de-duplicate and drop unknown/unavailable/empty states."""
    result: list[str] = []
    for s in states:
        s = str(s).strip()
        if s and s not in IGNORED_STATES and s not in result:
            result.append(s)
    return result


def remove_stale_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    subentry_id: str,
    platform_domain: str,
    expected: set[str | None],
) -> None:
    """Remove one tracker's entities of a platform whose unique_id is not expected."""
    registry = er.async_get(hass)
    for reg in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            reg.config_subentry_id == subentry_id
            and reg.domain == platform_domain
            and reg.unique_id not in expected
        ):
            registry.async_remove(reg.entity_id)
