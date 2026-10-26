"""Config and options flow for State Duration Tracker."""
from __future__ import annotations

from typing import Any

try:  # Home Assistant 2026.10+ validates with probatio (API compatible with voluptuous)
    import probatio as vol
except ImportError:  # pragma: no cover
    import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlowWithReload
from homeassistant.const import CONF_ENTITY_ID, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import selector

from .const import (
    CONF_ALERT_MINUTES,
    CONF_ALERT_STATE,
    CONF_DAILY_STATS,
    CONF_STATES,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
)
from .util import clean_states, option_label, suggest_states


def _states_selector(hass: HomeAssistant, entity_id: str, extra: list[str]) -> selector.SelectSelector:
    options = suggest_states(hass, entity_id)
    options += [s for s in extra if s not in options]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=[selector.SelectOptionDict(value=s, label=option_label(hass, entity_id, s)) for s in options],
            multiple=True,
            custom_value=True,
            sort=False,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _minutes_selector(min_value: int, max_value: int, unit: str = "min") -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=min_value, max=max_value, step=1, mode=selector.NumberSelectorMode.BOX, unit_of_measurement=unit
        )
    )


def _settings_fields(alert_choices: list[str], hass: HomeAssistant, entity_id: str, *, custom_alert: bool) -> dict:
    return {
        vol.Optional(CONF_DAILY_STATS, default=False): selector.BooleanSelector(),
        vol.Optional(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): _minutes_selector(1, 60),
        vol.Optional(CONF_ALERT_STATE): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value=s, label=option_label(hass, entity_id, s)) for s in alert_choices
                ],
                custom_value=custom_alert,
                mode=selector.SelectSelectorMode.DROPDOWN,
            )
        ),
        vol.Optional(CONF_ALERT_MINUTES): _minutes_selector(1, 100000),
    }


def _validate_alert(states: list[str], data: dict[str, Any]) -> dict[str, str]:
    alert_state = data.get(CONF_ALERT_STATE)
    minutes = data.get(CONF_ALERT_MINUTES)
    if bool(alert_state) != bool(minutes):
        return {"base": "alert_incomplete"}
    if alert_state and alert_state not in states:
        return {CONF_ALERT_STATE: "alert_state_not_tracked"}
    return {}


def _build_options(entity_id: str, states: list[str], data: dict[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        CONF_ENTITY_ID: entity_id,
        CONF_STATES: states,
        CONF_DAILY_STATS: bool(data.get(CONF_DAILY_STATS, False)),
        CONF_UPDATE_INTERVAL: int(data.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL)),
    }
    if data.get(CONF_ALERT_STATE) and data.get(CONF_ALERT_MINUTES):
        options[CONF_ALERT_STATE] = data[CONF_ALERT_STATE]
        options[CONF_ALERT_MINUTES] = int(data[CONF_ALERT_MINUTES])
    return options


class StateDurationConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self) -> None:
        self._entity_id: str = ""
        self._name: str = ""
        self._states: list[str] = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._entity_id = user_input[CONF_ENTITY_ID]
            return await self.async_step_states()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ENTITY_ID): selector.EntitySelector()}),
        )

    async def async_step_states(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        state = self.hass.states.get(self._entity_id)
        friendly = state.name if state else self._entity_id

        if user_input is not None:
            states = clean_states(user_input.get(CONF_STATES, []))
            if states:
                self._name = user_input[CONF_NAME]
                self._states = states
                return await self.async_step_settings()
            errors[CONF_STATES] = "no_states"

        default = (user_input or {}).get(CONF_STATES) or suggest_states(self.hass, self._entity_id)
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=(user_input or {}).get(CONF_NAME, friendly)): selector.TextSelector(),
                vol.Required(CONF_STATES, default=default): _states_selector(self.hass, self._entity_id, list(default)),
            }
        )
        return self.async_show_form(
            step_id="states",
            data_schema=schema,
            errors=errors,
            description_placeholders={"entity_id": self._entity_id},
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_alert(self._states, user_input)
            if not errors:
                return self.async_create_entry(
                    title=self._name, data={}, options=_build_options(self._entity_id, self._states, user_input)
                )
        schema = vol.Schema(_settings_fields(self._states, self.hass, self._entity_id, custom_alert=False))
        return self.async_show_form(
            step_id="settings",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlowWithReload:
        return StateDurationOptionsFlow()


class StateDurationOptionsFlow(OptionsFlowWithReload):
    """Edit tracked states and settings; the entry reloads automatically."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        current = dict(self.config_entry.options)
        entity_id = current[CONF_ENTITY_ID]

        if user_input is not None:
            states = clean_states(user_input.get(CONF_STATES, []))
            if not states:
                errors[CONF_STATES] = "no_states"
            else:
                errors = _validate_alert(states, user_input)
            if not errors:
                return self.async_create_entry(data=_build_options(entity_id, states, user_input))

        schema = vol.Schema(
            {
                vol.Required(CONF_STATES, default=current[CONF_STATES]): _states_selector(
                    self.hass, entity_id, list(current[CONF_STATES])
                ),
                **_settings_fields(list(current[CONF_STATES]), self.hass, entity_id, custom_alert=True),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or current),
            errors=errors,
            description_placeholders={"entity_id": entity_id},
        )
