# State Duration Tracker

A HACS custom integration that tracks **how long an entity has been in a state**, and survives restarts and `unknown` / `unavailable` blips.

Tested against Home Assistant **2026.10.4** (15 automated tests in `tests/`). Requires 2026.10+.

## Install
1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Download, then restart Home Assistant.
3. Settings → Devices & Services → **Helpers** → **Create helper** → **State Duration Tracker**.

## Use
1. Pick an entity (window, AC, door, select...).
2. Pick the states to track. Suggestions come from the entity (`hvac_modes`, `options`, or its domain); you can also type custom states.
   - Window: `on` (Open) and `off` (Closed)
   - AC: `off`, `heat`, `cool`, ...
3. Optional settings: daily totals/counts, update interval, "too long" alert.

Sensors are **linked to the source entity's device** (they never join or own it, as required by HA 2026.8+). If the source has no device, names are prefixed with the helper's name.

| Entity | Meaning |
|---|---|
| Current state duration | Time in the current tracked state. Attributes: `tracked_state`, `since`. Unknown in an untracked state. |
| Current state since | Timestamp the current tracked state began. |
| `<State> duration` | Time in that state, `0` while in another state. |
| `<State> today` / `<State> count today` | Optional. Total time and number of entries since local midnight (long-term statistics enabled). |
| `<State> too long` | Optional binary sensor, on once the entity has stayed in the alert state for N minutes (fires at the exact moment, not on the next refresh). |

Durations default to minutes (change the unit in entity settings). Use **Configure** on the helper to change states/settings; sensors of removed states are deleted.

## Rules
- `unknown` / `unavailable` never start, stop or reset a timer: `open → unavailable → open` keeps counting.
- Going to a state you did not select stops the timer.
- Start time and daily totals are stored on disk, so restarts don't reset them.
- Renaming the source entity, or moving it to another device, is followed automatically.
- If the entity changed while HA was down, the timer restarts from HA startup.

## Database load
Duration sensors write a new value every update interval (default 1 min): about 2 rows/min per tracked entity. Raise the interval (e.g. 5 min) or exclude the sensors from the recorder if you only need the timestamp or the alert.
