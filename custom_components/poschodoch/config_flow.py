"""Config flow for poschodoch.sk."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PoschodochApiClient, PoschodochAuthError
from .const import (
    CONF_ID_REFRESH_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_AFTER,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {vol.Required("id_token"): str, vol.Required("refresh_token"): str}
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
        await client.get_menu_map()
        return {
            CONF_ID_TOKEN: client._id_token,
            CONF_ID_REFRESH_TOKEN: client._id_refresh_token,
            CONF_TOKEN_EXPIRES_AT: client._token_expires_at.isoformat(),
            CONF_REFRESH_AFTER: client._refresh_after.isoformat(),
        }

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = await self._validate_and_build_entry_data(
                    user_input["id_token"], user_input["refresh_token"]
                )
            except PoschodochAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
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
                    user_input["id_token"], user_input["refresh_token"]
                )
            except PoschodochAuthError:
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
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
