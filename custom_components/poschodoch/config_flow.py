"""Config flow for poschodoch.sk."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PoschodochApiClient, PoschodochAuthError
from .const import CONF_ID_REFRESH_TOKEN, CONF_ID_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_ID_TOKEN): str, vol.Required(CONF_ID_REFRESH_TOKEN): str}
)


class PoschodochConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for poschodoch.sk."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None

    async def _validate_and_build_entry_data(self, id_token: str, refresh_token: str) -> dict:
        now = datetime.now(timezone.utc)
        client = PoschodochApiClient(
            id_token=id_token,
            id_refresh_token=refresh_token,
            token_expires_at=now,
            refresh_after=now,
            session=async_get_clientsession(self.hass),
        )
        # A pair copied straight out of the browser has never been used
        # with this API client before, so it must be bound to a unit via
        # Auth/changeunit before anything else works. This also updates
        # token_expires_at/refresh_after for all subsequent calls.
        await client.activate()
        await client.get_menu_map()
        return client.token_state

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate_and_build_entry_data(
                    user_input[CONF_ID_TOKEN], user_input[CONF_ID_REFRESH_TOKEN]
                )
            except PoschodochAuthError:
                _LOGGER.exception("poschodoch.sk rejected the provided tokens")
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error validating poschodoch.sk tokens")
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title="poschodoch.sk", data=data)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate_and_build_entry_data(
                    user_input[CONF_ID_TOKEN], user_input[CONF_ID_REFRESH_TOKEN]
                )
            except PoschodochAuthError:
                _LOGGER.exception("poschodoch.sk rejected the provided tokens")
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error validating poschodoch.sk tokens")
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry, data=data
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )
