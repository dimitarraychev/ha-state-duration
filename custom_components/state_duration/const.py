"""Constants for State Duration Tracker."""
from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

DOMAIN = "state_duration"

CONF_STATES = "states"
CONF_DAILY_STATS = "daily_stats"
CONF_UPDATE_INTERVAL = "update_interval"  # minutes
CONF_ALERT_STATE = "alert_state"
CONF_ALERT_MINUTES = "alert_minutes"

DEFAULT_UPDATE_INTERVAL = 1
STORAGE_VERSION = 1

# These are never treated as a state: they neither start, stop nor reset a timer.
IGNORED_STATES = frozenset({STATE_UNKNOWN, STATE_UNAVAILABLE})

_OPEN = ("Open", "Closed")
BINARY_SENSOR_LABELS: dict[str, tuple[str, str]] = {
    "door": _OPEN,
    "window": _OPEN,
    "garage_door": _OPEN,
    "opening": _OPEN,
    "lock": ("Unlocked", "Locked"),
    "motion": ("Motion", "Clear"),
    "moving": ("Moving", "Stopped"),
    "occupancy": ("Occupied", "Clear"),
    "presence": ("Home", "Away"),
    "moisture": ("Wet", "Dry"),
    "connectivity": ("Connected", "Disconnected"),
    "plug": ("Plugged in", "Unplugged"),
    "power": ("Power", "No power"),
    "problem": ("Problem", "OK"),
    "smoke": ("Smoke", "Clear"),
    "vibration": ("Vibration", "Clear"),
    "light": ("Light", "No light"),
}

_ON_OFF = ("on", "off")
DOMAIN_STATES: dict[str, tuple[str, ...]] = {
    "binary_sensor": _ON_OFF,
    "switch": _ON_OFF,
    "light": _ON_OFF,
    "input_boolean": _ON_OFF,
    "fan": _ON_OFF,
    "humidifier": _ON_OFF,
    "cover": ("open", "closed", "opening", "closing"),
    "lock": ("locked", "unlocked", "locking", "unlocking", "jammed", "open"),
    "climate": ("off", "heat", "cool", "heat_cool", "auto", "dry", "fan_only"),
    "media_player": ("off", "on", "idle", "playing", "paused", "standby", "buffering"),
    "person": ("home", "not_home"),
    "device_tracker": ("home", "not_home"),
    "vacuum": ("docked", "cleaning", "returning", "paused", "idle", "error"),
    "water_heater": ("off", "eco", "electric", "performance", "high_demand", "heat_pump", "gas"),
    "alarm_control_panel": (
        "disarmed", "armed_home", "armed_away", "armed_night",
        "armed_vacation", "armed_custom_bypass", "pending", "arming", "triggered",
    ),
}
