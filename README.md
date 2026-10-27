# State Duration Tracker

A HACS custom integration that tracks **how long an entity has been in a state**, and survives restarts and `unknown` / `unavailable` blips. It works like Battery Notes: one integration entry, and you add a *tracker* per entity.

Tested against Home Assistant **2026.10.4** (17 automated tests in `tests/`). Requires 2026.10+.

## Install
1. HACS → ⋮ → **Custom repositories** → add this repo, category **Integration**.
2. Download, then restart Home Assistant.
3. Settings → Devices & Services → **Add integration** → **State Duration Tracker** (once).

## Add a tracker
On the State Duration Tracker integration page click **Add tracker**:
1. Pick an entity (window, AC, door, select...).
2. Pick the states to track. Suggestions come from the entity (`hvac_modes`, `options`, or its domain); you can also type custom states.
   - Window: `on` (Open) and `off` (Closed)
   - AC: `off`, `heat`, `cool`, ...
3. Optional: daily totals, update interval, a "too long" alert.

Use the **cog** next to a tracker to change its name, states or settings. Delete a tracker from its ⋮ menu; its sensors and stored data are removed.

## Entities
Sensors are **linked to the source entity's device** (they never join or own it, as required by HA 2026.8+) and listed under their tracker. If the source has no device, names are prefixed with the tracker name.

| Entity | Meaning |
|---|---|
| Current state duration | Time in the current tracked state. Attributes: `tracked_state`, `since`. Unknown in an untracked state. |
| Current state since | Timestamp the current tracked state began. |
| `<State> duration` | Time in that state, `0` while in another state. |
| `<State> today` / `<State> count today` | Optional. Total time and number of entries since local midnight (long-term statistics enabled). |
| `<State> too long` | Optional binary sensor, on once the entity has stayed in the alert state for N minutes (fires at the exact moment). |

Durations default to minutes (change the unit in entity settings).

## Rules
- `unknown` / `unavailable` never start, stop or reset a timer: `open → unavailable → open` keeps counting.
- Going to a state you did not select stops the timer.
- Start time and daily totals are stored on disk, so restarts don't reset them.
- Renaming the source entity, or moving it to another device, is followed automatically (per tracker).
- If the entity changed while HA was down, the timer restarts from HA startup.

## Database load
Duration sensors write a new value every update interval (default 1 min): about 2 rows/min per tracker. Raise the interval (e.g. 5 min) or exclude the sensors from the recorder if you only need the timestamp or the alert.
