"""Behavioural tests for State Duration Tracker (run against real Home Assistant)."""
from datetime import timedelta

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState, ConfigSubentryData
from homeassistant.data_entry_flow import InvalidData
from homeassistant.helpers import device_registry as dr, entity_registry as er

from custom_components.state_duration.const import DOMAIN

SRC = "binary_sensor.bathroom_window"
KITCHEN = "binary_sensor.kitchen_window"
START = "2026-10-26 12:00:00+00:00"  # 05:00 US/Pacific: no midnight crossing
TYPE = "tracker"


def tracker_data(entity_id=SRC, **opts):
    data = {"entity_id": entity_id, "states": ["on", "off"], "daily_stats": False, "update_interval": 1}
    data.update(opts)
    return data


def sub(sid="bath", title="Bathroom Window", **kw) -> ConfigSubentryData:
    entity_id = kw.pop("entity_id", SRC)
    return ConfigSubentryData(
        data=tracker_data(entity_id, **kw), subentry_type=TYPE, title=title, unique_id=None, subentry_id=sid
    )


def make_entry(*subentries: ConfigSubentryData, **opts) -> MockConfigEntry:
    subs = list(subentries) or [sub(**opts)]
    return MockConfigEntry(domain=DOMAIN, title="State Duration Tracker", data={}, subentries_data=subs)


def set_src(hass, state, entity_id=SRC):
    hass.states.async_set(entity_id, state, {"device_class": "window"})


def eid(hass, suffix, domain="sensor", sid="bath"):
    ent = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{sid}_{suffix}")
    assert ent, f"no {domain} entity for {sid}_{suffix}"
    return ent


def own(hass, entry, sid="bath"):
    reg = er.async_get(hass)
    return [e for e in er.async_entries_for_config_entry(reg, entry.entry_id) if e.config_subentry_id == sid]


async def tick(hass, freezer, **delta):
    freezer.tick(timedelta(**delta))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def minutes(hass, entity_id) -> float:
    return float(hass.states.get(entity_id).state)


def add_source(hass, name, object_id, uid):
    """A window sensor that belongs to another integration's device."""
    cfg = MockConfigEntry(domain="test", entry_id=f"cfg_{uid}")
    cfg.add_to_hass(hass)
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=cfg.entry_id, identifiers={("test", uid)}, name=name
    )
    er.async_get(hass).async_get_or_create(
        "binary_sensor", "test", f"{uid}_contact", config_entry=cfg, device_id=dev.id,
        suggested_object_id=object_id,
    )
    set_src(hass, "off", f"binary_sensor.{object_id}")
    return cfg, dev


@pytest.fixture
async def source(hass, freezer):
    freezer.move_to(START)
    return add_source(hass, "Bathroom window sensor", "bathroom_window", "win1")


async def setup(hass, entry):
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- timer logic -----------------------------------------------------------
async def test_timer_survives_unavailable(hass, hass_storage, source, freezer):
    await setup(hass, make_entry())
    set_src(hass, "on")
    await hass.async_block_till_done()
    await tick(hass, freezer, minutes=10)
    cur = eid(hass, "current_duration")
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
    assert minutes(hass, eid(hass, "state_on")) == 0
    assert minutes(hass, eid(hass, "state_off")) == pytest.approx(4, abs=0.1)


async def test_untracked_state_stops_timer(hass, hass_storage, source, freezer):
    await setup(hass, make_entry(states=["on"]))
    cur = eid(hass, "current_duration")
    set_src(hass, "on")
    await tick(hass, freezer, minutes=5)
    assert minutes(hass, cur) == pytest.approx(5, abs=0.1)
    set_src(hass, "off")  # not tracked
    await tick(hass, freezer, minutes=1)
    assert hass.states.get(cur).state == "unknown"


async def test_restart_keeps_start_time(hass, hass_storage, source, freezer):
    entry = make_entry()
    await setup(hass, entry)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=10)

    assert await hass.config_entries.async_unload(entry.entry_id)
    set_src(hass, "unavailable")  # source not back yet when we restart
    await tick(hass, freezer, minutes=5)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    cur = eid(hass, "current_duration")
    assert minutes(hass, cur) == pytest.approx(15, abs=0.1)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=1)
    assert minutes(hass, cur) == pytest.approx(16, abs=0.1)


# --- devices & registry ----------------------------------------------------
async def test_each_tracker_links_to_its_own_source_device(hass, hass_storage, source):
    cfg, dev = source
    kcfg, kdev = add_source(hass, "Kitchen window sensor", "kitchen_window", "win2")
    entry = make_entry(sub("bath", "Bathroom", daily_stats=True), sub("kitch", "Kitchen", entity_id=KITCHEN))
    await setup(hass, entry)

    bath = own(hass, entry, "bath")
    assert len(bath) == 2 + 2 * 3  # current x2, plus per state: duration/today/count
    assert all(e.device_id == dev.id for e in bath)
    assert all(e.device_id == kdev.id for e in own(hass, entry, "kitch"))
    dev_reg = dr.async_get(hass)
    assert dev_reg.async_get(dev.id).config_entry_id == cfg.entry_id  # still owned by the source
    assert dr.async_entries_for_config_entry(dev_reg, entry.entry_id) == []  # we own no device


async def test_follows_source_entity_rename(hass, hass_storage, source):
    entry = make_entry()
    await setup(hass, entry)
    er.async_get(hass).async_update_entity(SRC, new_entity_id="binary_sensor.bath_window")
    await hass.async_block_till_done()
    assert entry.subentries["bath"].data["entity_id"] == "binary_sensor.bath_window"
    assert entry.state is ConfigEntryState.LOADED


async def test_moving_one_source_does_not_touch_other_trackers(hass, hass_storage, source):
    _, dev = source
    _, kdev = add_source(hass, "Kitchen window sensor", "kitchen_window", "win2")
    cfg3 = MockConfigEntry(domain="test", entry_id="cfg_new")
    cfg3.add_to_hass(hass)
    newdev = dr.async_get(hass).async_get_or_create(
        config_entry_id=cfg3.entry_id, identifiers={("test", "new")}, name="New device"
    )
    entry = make_entry(sub("bath", "Bathroom"), sub("kitch", "Kitchen", entity_id=KITCHEN))
    await setup(hass, entry)

    er.async_get(hass).async_update_entity(SRC, device_id=newdev.id)
    await hass.async_block_till_done()
    assert all(e.device_id == newdev.id for e in own(hass, entry, "bath"))
    assert all(e.device_id == kdev.id for e in own(hass, entry, "kitch"))
    assert entry.state is ConfigEntryState.LOADED


# --- daily stats, alert ----------------------------------------------------
async def test_daily_totals_and_counts(hass, hass_storage, source, freezer):
    await setup(hass, make_entry(daily_stats=True))
    set_src(hass, "on")
    await tick(hass, freezer, minutes=30)
    set_src(hass, "off")
    await tick(hass, freezer, minutes=10)
    set_src(hass, "on")
    await tick(hass, freezer, minutes=5)
    assert minutes(hass, eid(hass, "today_on")) == pytest.approx(35, abs=0.1)
    assert minutes(hass, eid(hass, "count_on")) == 2
    assert minutes(hass, eid(hass, "today_off")) == pytest.approx(10, abs=0.1)


async def test_daily_totals_roll_over_at_midnight(hass, hass_storage, source, freezer):
    freezer.move_to("2026-10-27 06:50:00+00:00")  # 23:50 US/Pacific
    await setup(hass, make_entry(daily_stats=True))
    set_src(hass, "on")
    await tick(hass, freezer, minutes=40)  # now 00:30 next day
    assert minutes(hass, eid(hass, "today_on")) == pytest.approx(30, abs=0.1)
    assert minutes(hass, eid(hass, "count_on")) == 0  # entered yesterday


async def test_alert_turns_on_exactly_after_threshold(hass, hass_storage, source, freezer):
    await setup(hass, make_entry(alert_state="on", alert_minutes=30, update_interval=60))
    alert = eid(hass, "alert", "binary_sensor")
    set_src(hass, "on")
    await tick(hass, freezer, minutes=29)
    assert hass.states.get(alert).state == "off"
    await tick(hass, freezer, minutes=2)  # crosses 30 min long before the 60 min refresh
    assert hass.states.get(alert).state == "on"
    set_src(hass, "off")
    await hass.async_block_till_done()
    assert hass.states.get(alert).state == "off"


# --- naming ----------------------------------------------------------------
async def test_names_with_device(hass, hass_storage, source):
    await setup(hass, make_entry())
    state = hass.states.get("sensor.bathroom_window_sensor_open_duration")
    assert state is not None
    assert state.attributes["friendly_name"] == "Bathroom window sensor Open duration"
    assert hass.states.get("sensor.bathroom_window_sensor_closed_duration") is not None


async def test_source_without_device(hass, hass_storage, freezer):
    freezer.move_to(START)
    set_src(hass, "off")
    entry = make_entry()
    await setup(hass, entry)
    ours = own(hass, entry)
    assert ours and all(e.device_id is None for e in ours)
    state = hass.states.get("sensor.bathroom_window_open_duration")
    assert state is not None
    assert state.attributes["friendly_name"] == "Bathroom Window Open duration"
    set_src(hass, "on")
    await tick(hass, freezer, minutes=7)
    assert minutes(hass, "sensor.bathroom_window_current_state_duration") == pytest.approx(7, abs=0.1)


# --- main entry & subentry flows ---------------------------------------------
async def test_main_flow_creates_single_service_entry(hass, hass_storage):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert again["type"] == "abort"
    assert again["reason"] == "single_instance_allowed"


async def test_add_tracker_via_subentry_flow(hass, hass_storage, source):
    entry = make_entry(sub("other", "Other"))
    await setup(hass, entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, TYPE), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"entity_id": SRC})
    assert result["step_id"] == "states"
    result = await flows.async_configure(
        result["flow_id"], {"name": "Bathroom Window", "states": ["on", "off", "unavailable"]}
    )
    assert result["step_id"] == "settings"
    result = await flows.async_configure(
        result["flow_id"], {"daily_stats": True, "update_interval": 1, "alert_state": "on", "alert_minutes": 30}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    new = next(s for s in entry.subentries.values() if s.title == "Bathroom Window")
    assert dict(new.data) == {
        "entity_id": SRC, "states": ["on", "off"], "daily_stats": True,
        "update_interval": 1, "alert_state": "on", "alert_minutes": 30,
    }
    assert entry.state is ConfigEntryState.LOADED
    assert eid(hass, "current_duration", sid=new.subentry_id)
    assert eid(hass, "alert", "binary_sensor", sid=new.subentry_id)


async def test_subentry_flow_validation(hass, hass_storage, source):
    entry = make_entry(sub("other", "Other"))
    await setup(hass, entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init((entry.entry_id, TYPE), context={"source": "user"})
    result = await flows.async_configure(result["flow_id"], {"entity_id": SRC})
    result = await flows.async_configure(result["flow_id"], {"name": "x", "states": ["unknown", "unavailable"]})
    assert result["errors"] == {"states": "no_states"}
    result = await flows.async_configure(result["flow_id"], {"name": "x", "states": ["on"]})
    result = await flows.async_configure(result["flow_id"], {"alert_state": "on"})
    assert result["errors"] == {"base": "alert_incomplete"}
    with pytest.raises(InvalidData):  # the dropdown only offers tracked states
        await flows.async_configure(result["flow_id"], {"alert_state": "off", "alert_minutes": 5})


async def test_reconfigure_renames_and_removes_stale_entities(hass, hass_storage, source):
    entry = make_entry(daily_stats=True)
    await setup(hass, entry)
    assert len(own(hass, entry)) == 8

    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, TYPE), context={"source": "reconfigure", "subentry_id": "bath"}
    )
    assert result["step_id"] == "reconfigure"
    result = await flows.async_configure(
        result["flow_id"], {"name": "Bath", "states": ["on"], "daily_stats": False, "update_interval": 1}
    )
    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert entry.subentries["bath"].title == "Bath"
    left = {e.unique_id.removeprefix("bath_") for e in own(hass, entry)}
    assert left == {"current_duration", "current_since", "state_on"}


async def test_reconfigure_alert_state_must_be_tracked(hass, hass_storage, source):
    entry = make_entry()
    await setup(hass, entry)
    flows = hass.config_entries.subentries
    result = await flows.async_init(
        (entry.entry_id, TYPE), context={"source": "reconfigure", "subentry_id": "bath"}
    )
    result = await flows.async_configure(
        result["flow_id"], {"name": "Bath", "states": ["on"], "alert_state": "off", "alert_minutes": 5}
    )
    assert result["errors"] == {"alert_state": "alert_state_not_tracked"}


async def test_removing_tracker_deletes_entities_and_stored_data(hass, hass_storage, source):
    entry = make_entry(sub("bath", "Bathroom"), sub("kitch", "Kitchen", entity_id=KITCHEN))
    add_source(hass, "Kitchen window sensor", "kitchen_window", "win2")
    await setup(hass, entry)
    await hass.config_entries.async_reload(entry.entry_id)  # reload saves trackers to storage
    await hass.async_block_till_done()
    assert "state_duration.bath" in hass_storage

    hass.config_entries.async_remove_subentry(entry, "bath")
    await hass.async_block_till_done()
    assert own(hass, entry, "bath") == []
    assert own(hass, entry, "kitch")  # untouched
    assert "state_duration.bath" not in hass_storage
    assert entry.state is ConfigEntryState.LOADED
