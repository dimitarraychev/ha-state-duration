"""Behavioural tests for State Duration Tracker (run against real Home Assistant)."""
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.state_duration.const import DOMAIN

SRC = "binary_sensor.bathroom_window"
START = "2026-10-26 12:00:00+00:00"  # 05:00 US/Pacific: no midnight crossing


def make_entry(**opts) -> MockConfigEntry:
    options = {"entity_id": SRC, "states": ["on", "off"], "daily_stats": False, "update_interval": 1}
    options.update(opts)
    return MockConfigEntry(domain=DOMAIN, title="Bathroom Window", data={}, options=options, version=2)


def set_src(hass, state):
    hass.states.async_set(SRC, state, {"device_class": "window"})


def eid(hass, entry, domain, suffix):
    ent = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")
    assert ent, f"no {domain} entity for {suffix}"
    return ent


async def tick(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def minutes(hass, entity_id) -> float:
    return float(hass.states.get(entity_id).state)


@pytest.fixture
async def source(hass, freezer):
    """A window sensor that belongs to another integration's device."""
    freezer.move_to(START)
    cfg = MockConfigEntry(domain="test")
    cfg.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=cfg.entry_id, identifiers={("test", "win1")}, name="Bathroom window sensor"
    )
    er.async_get(hass).async_get_or_create(
        "binary_sensor", "test", "win1_contact", config_entry=cfg, device_id=dev.id,
        suggested_object_id="bathroom_window",
    )
    set_src(hass, "off")
    return cfg, dev


async def setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- timer logic -----------------------------------------------------------
async def test_timer_survives_unavailable(hass, hass_storage, source, freezer):
    freezer.move_to(START)
    entry = make_entry()
    await setup(hass, entry)

    set_src(hass, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, minutes=10)
    cur = eid(hass, entry, "sensor", "current_duration")
    assert minutes(hass, cur) == pytest.approx(10, abs=0.1)
    assert hass.states.get(cur).attributes["tracked_state"] == "on"
    assert hass.states.get(cur).attributes["tracked_state_label"] == "Open"

    # on -> unavailable -> unknown -> on must NOT reset the timer
    set_src(hass, "unavailable")
    await tick(hass, freezer, minutes=3)
    set_src(hass, "unknown")
    await tick(hass, freezer, minutes=2)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=1)
    assert minutes(hass, cur) == pytest.approx(16, abs=0.1)

    # a real change starts a new timer
    set_src(hass, "off")
    await tick(hass, freezer, minutes=4)
    assert hass.states.get(cur).attributes["tracked_state"] == "off"
    assert minutes(hass, cur) == pytest.approx(4, abs=0.1)
    assert minutes(hass, eid(hass, entry, "sensor", "state_on")) == 0
    assert minutes(hass, eid(hass, entry, "sensor", "state_off")) == pytest.approx(4, abs=0.1)


async def test_untracked_state_stops_timer(hass, hass_storage, source, freezer):
    freezer.move_to(START)
    entry = make_entry(states=["on"])
    await setup(hass, entry)
    cur = eid(hass, entry, "sensor", "current_duration")

    set_src(hass, "on")
    await tick(hass, freezer, minutes=5)
    assert minutes(hass, cur) == pytest.approx(5, abs=0.1)
    set_src(hass, "off")  # not tracked
    await tick(hass, freezer, minutes=1)
    assert hass.states.get(cur).state == "unknown"


async def test_restart_keeps_start_time(hass, hass_storage, source, freezer):
    freezer.move_to(START)
    entry = make_entry()
    await setup(hass, entry)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=10)

    assert await hass.config_entries.async_unload(entry.entry_id)
    set_src(hass, "unavailable")  # source not back yet when we restart
    await tick(hass, freezer, minutes=5)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    cur = eid(hass, entry, "sensor", "current_duration")
    assert minutes(hass, cur) == pytest.approx(15, abs=0.1)
    set_src(hass, "on")  # comes back in the same state
    await tick(hass, freezer, minutes=1)
    assert minutes(hass, cur) == pytest.approx(16, abs=0.1)


# --- devices & registry ----------------------------------------------------
async def test_entities_link_to_source_device_without_joining_it(hass, hass_storage, source):
    cfg, dev = source
    entry = make_entry(daily_stats=True)
    await setup(hass, entry)
    reg = er.async_get(hass)
    ours = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert len(ours) == 2 + 2 * 3  # current x2, plus per state: duration/today/count
    assert all(e.device_id == dev.id for e in ours)
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get(dev.id).config_entry_id == cfg.entry_id  # still owned by the source
    assert dr.async_entries_for_config_entry(dev_reg, entry.entry_id) == []  # we own no device


async def test_follows_source_entity_rename(hass, hass_storage, source):
    entry = make_entry()
    await setup(hass, entry)
    er.async_get(hass).async_update_entity(SRC, new_entity_id="binary_sensor.bath_window")
    await hass.async_block_till_done()
    assert entry.options["entity_id"] == "binary_sensor.bath_window"
    assert entry.state is ConfigEntryState.LOADED


async def test_migrates_v1_entry(hass, hass_storage, source):
    entry = MockConfigEntry(
        domain=DOMAIN, title="Old", version=1, data={"entity_id": SRC, "states": ["on", "off"]}
    )
    await setup(hass, entry)
    assert entry.version == 2
    assert entry.options["states"] == ["on", "off"]
    assert entry.state is ConfigEntryState.LOADED


# --- daily stats, alert ----------------------------------------------------
async def test_daily_totals_and_counts(hass, hass_storage, source, freezer):
    freezer.move_to(START)
    entry = make_entry(daily_stats=True)
    await setup(hass, entry)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=30)
    set_src(hass, "off")
    await tick(hass, freezer, minutes=10)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=5)
    assert minutes(hass, eid(hass, entry, "sensor", "today_on")) == pytest.approx(35, abs=0.1)
    assert minutes(hass, eid(hass, entry, "sensor", "count_on")) == 2
    assert minutes(hass, eid(hass, entry, "sensor", "today_off")) == pytest.approx(10, abs=0.1)


async def test_daily_totals_roll_over_at_midnight(hass, hass_storage, source, freezer):
    freezer.move_to("2026-10-27 06:50:00+00:00")  # 23:50 US/Pacific
    entry = make_entry(daily_stats=True)
    await setup(hass, entry)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=40)  # now 00:30 next day
    assert minutes(hass, eid(hass, entry, "sensor", "today_on")) == pytest.approx(30, abs=0.1)
    assert minutes(hass, eid(hass, entry, "sensor", "count_on")) == 0  # entered yesterday


async def test_alert_turns_on_exactly_after_threshold(hass, hass_storage, source, freezer):
    freezer.move_to(START)
    entry = make_entry(alert_state="on", alert_minutes=30, update_interval=60)
    await setup(hass, entry)
    alert = eid(hass, entry, "binary_sensor", "alert")
    set_src(hass, "on")
    await tick(hass, freezer, minutes=29)
    assert hass.states.get(alert).state == "off"
    await tick(hass, freezer, minutes=2)  # crosses 30 min long before the 60 min refresh
    assert hass.states.get(alert).state == "on"
    set_src(hass, "off")
    await hass.async_block_till_done()
    assert hass.states.get(alert).state == "off"


# --- config & options flow -------------------------------------------------
async def test_config_flow_creates_entry(hass, hass_storage, source):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entity_id": SRC})
    assert result["step_id"] == "states"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Bathroom Window", "states": ["on", "off", "unavailable"]}
    )
    assert result["step_id"] == "settings"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"daily_stats": True, "update_interval": 1, "alert_state": "on", "alert_minutes": 30},
    )
    assert result["type"] == "create_entry"
    assert result["options"] == {
        "entity_id": SRC, "states": ["on", "off"], "daily_stats": True,
        "update_interval": 1, "alert_state": "on", "alert_minutes": 30,
    }
    await hass.async_block_till_done()
    assert hass.config_entries.async_entries(DOMAIN)[0].state is ConfigEntryState.LOADED


async def test_config_flow_validation(hass, hass_storage, source):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"entity_id": SRC})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "states": ["unknown", "unavailable"]}
    )
    assert result["errors"] == {"states": "no_states"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "x", "states": ["on"]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"alert_state": "on"})
    assert result["errors"] == {"base": "alert_incomplete"}
    # the setup dropdown only offers tracked states, so the schema itself rejects others
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"alert_state": "off", "alert_minutes": 5}
        )


async def test_options_flow_alert_state_must_be_tracked(hass, hass_storage, source):
    entry = make_entry()
    await setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"states": ["on"], "alert_state": "off", "alert_minutes": 5}
    )
    assert result["errors"] == {"alert_state": "alert_state_not_tracked"}


async def test_options_flow_removes_stale_entities(hass, hass_storage, source):
    entry = make_entry(daily_stats=True)
    await setup(hass, entry)
    reg = er.async_get(hass)
    assert len(er.async_entries_for_config_entry(reg, entry.entry_id)) == 8

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"states": ["on"], "daily_stats": False, "update_interval": 1}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    left = {e.unique_id.removeprefix(entry.entry_id + "_") for e in er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert left == {"current_duration", "current_since", "state_on"}


# --- naming ----------------------------------------------------------------
async def test_names_with_device(hass, hass_storage, source):
    entry = make_entry()
    await setup(hass, entry)
    state = hass.states.get("sensor.bathroom_window_sensor_open_duration")
    assert state is not None
    assert state.attributes["friendly_name"] == "Bathroom window sensor Open duration"
    assert hass.states.get("sensor.bathroom_window_sensor_closed_duration") is not None


async def test_source_without_device(hass, hass_storage, freezer):
    """A source entity with no device still works; names fall back to the title."""
    freezer.move_to(START)
    set_src(hass, "off")
    entry = make_entry()
    await setup(hass, entry)
    reg = er.async_get(hass)
    ours = er.async_entries_for_config_entry(reg, entry.entry_id)
    assert ours and all(e.device_id is None for e in ours)
    state = hass.states.get("sensor.bathroom_window_open_duration")
    assert state is not None
    assert state.attributes["friendly_name"] == "Bathroom Window Open duration"
    set_src(hass, "on")
    await tick(hass, freezer, minutes=7)
    assert minutes(hass, "sensor.bathroom_window_current_state_duration") == pytest.approx(7, abs=0.1)
