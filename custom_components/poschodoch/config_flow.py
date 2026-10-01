"""Config flow for poschodoch.sk."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PoschodochApiClient, PoschodochAuthError, _password_login
from .const import (
    CONF_ID_REFRESH_TOKEN,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

LOGIN_MENU_OPTIONS = ["password", "manual"]

STEP_MANUAL_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_ID_TOKEN): str, vol.Required(CONF_ID_REFRESH_TOKEN): str}
)
STEP_PASSWORD_DATA_SCHEMA = vol.Schema(
    {vol.Required(CONF_USERNAME): str, vol.Required(CONF_PASSWORD): str}
)
STEP_TWO_FACTOR_DATA_SCHEMA = vol.Schema({vol.Required("code"): str})


class PoschodochConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for poschodoch.sk."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: config_entries.ConfigEntry | None = None
        self._pending_username: str | None = None
        self._pending_password: str | None = None

    def _finish(self, data: dict):
        if self._reauth_entry is not None:
            return self.async_update_reload_and_abort(self._reauth_entry, data=data)
        return self.async_create_entry(title="poschodoch.sk", data=data)

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

    async def _complete_password_login(self, login_result: dict) -> dict:
        now = datetime.now(timezone.utc)
        client = PoschodochApiClient(
            id_token=login_result["auth_token"],
            id_refresh_token=login_result["refresh_token"],
            token_expires_at=now,
            refresh_after=now,
            session=async_get_clientsession(self.hass),
            username=self._pending_username,
            password=self._pending_password,
            device_cookie=login_result["device_cookie"],
        )
        # Auth/changeunit is required after a password login too, same as
        # after a token refresh — identical tail to the manual method.
        await client.activate()
        await client.get_menu_map()
        return {
            **client.token_state,
            CONF_USERNAME: self._pending_username,
            CONF_PASSWORD: self._pending_password,
        }

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=LOGIN_MENU_OPTIONS)

    async def async_step_manual(self, user_input=None):
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
                return self._finish(data)

        return self.async_show_form(
            step_id="manual", data_schema=STEP_MANUAL_DATA_SCHEMA, errors=errors
        )

    async def async_step_password(self, user_input=None):
        errors = {}
        if user_input is not None:
            self._pending_username = user_input[CONF_USERNAME]
            self._pending_password = user_input[CONF_PASSWORD]
            try:
                result = await _password_login(
                    async_get_clientsession(self.hass),
                    self._pending_username,
                    self._pending_password,
                )
            except PoschodochAuthError:
                _LOGGER.exception("poschodoch.sk rejected the provided credentials")
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception(
                    "Unexpected error validating poschodoch.sk credentials"
                )
                errors["base"] = "cannot_connect"
            else:
                if result["requires_two_factor"]:
                    return await self.async_step_two_factor()
                data = await self._complete_password_login(result)
                return self._finish(data)

        return self.async_show_form(
            step_id="password", data_schema=STEP_PASSWORD_DATA_SCHEMA, errors=errors
        )

    async def async_step_two_factor(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                result = await _password_login(
                    async_get_clientsession(self.hass),
                    self._pending_username,
                    self._pending_password,
                    two_factor_code=user_input["code"],
                )
            except PoschodochAuthError:
                _LOGGER.exception("poschodoch.sk rejected the provided 2FA code")
                errors["base"] = "invalid_auth"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error validating poschodoch.sk 2FA code")
                errors["base"] = "cannot_connect"
            else:
                if result["requires_two_factor"]:
                    # Shouldn't happen (a code was just supplied) — don't
                    # loop forever on a surprising server response.
                    errors["base"] = "invalid_auth"
                else:
                    data = await self._complete_password_login(result)
                    return self._finish(data)

        return self.async_show_form(
            step_id="two_factor", data_schema=STEP_TWO_FACTOR_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(self, entry_data):
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        return self.async_show_menu(
            step_id="reauth_confirm", menu_options=LOGIN_MENU_OPTIONS
        )
