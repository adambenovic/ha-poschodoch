"""The poschodoch.sk integration."""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import statistics
from .api import PoschodochApiClient
from .const import (
    CONF_DEVICE_COOKIE,
    CONF_ID_REFRESH_TOKEN,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_AFTER,
    CONF_TOKEN_EXPIRES_AT,
    CONF_USERNAME,
    DOMAIN,
)
from .coordinator import PoschodochDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up poschodoch.sk from a config entry."""

    async def on_tokens_updated(id_token: str, id_refresh_token: str) -> None:
        # client.token_state already reflects the rotation that just
        # happened (set in _activate_unit right before calling back) —
        # persist the expiry fields too, not just the tokens, or a restart
        # right after a mid-session rotation starts back up with the stale
        # timestamps from whenever the entry was first created.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, **client.token_state}
        )

    client = PoschodochApiClient(
        id_token=entry.data[CONF_ID_TOKEN],
        id_refresh_token=entry.data[CONF_ID_REFRESH_TOKEN],
        token_expires_at=datetime.fromisoformat(entry.data[CONF_TOKEN_EXPIRES_AT]),
        refresh_after=datetime.fromisoformat(entry.data[CONF_REFRESH_AFTER]),
        session=async_get_clientsession(hass),
        on_tokens_updated=on_tokens_updated,
        # Only present on entries set up via email+password login — absent
        # for existing token-paste entries, which keeps self-heal a no-op
        # for them (unchanged behavior).
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
        device_cookie=entry.data.get(CONF_DEVICE_COOKIE),
    )

    coordinator = PoschodochDataUpdateCoordinator(hass, client)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _run_backfill() -> None:
        try:
            await statistics.async_backfill(hass, entry, client)
        except Exception:  # pylint: disable=broad-except
            # Supplementary long-term stats — must never affect the
            # integration's own loaded state.
            _LOGGER.warning(
                "Failed to backfill poschodoch.sk long-term statistics",
                exc_info=True,
            )

    entry.async_create_background_task(
        hass, _run_backfill(), "poschodoch_stats_backfill"
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.client.close()
    return unload_ok
