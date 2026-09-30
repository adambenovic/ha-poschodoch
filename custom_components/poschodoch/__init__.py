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
    CONF_ID_REFRESH_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_AFTER,
    CONF_STATS_BACKFILLED,
    CONF_TOKEN_EXPIRES_AT,
    DOMAIN,
)
from .coordinator import PoschodochDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up poschodoch.sk from a config entry."""

    async def on_tokens_updated(id_token: str, id_refresh_token: str) -> None:
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ID_TOKEN: id_token,
                CONF_ID_REFRESH_TOKEN: id_refresh_token,
            },
        )

    client = PoschodochApiClient(
        id_token=entry.data[CONF_ID_TOKEN],
        id_refresh_token=entry.data[CONF_ID_REFRESH_TOKEN],
        token_expires_at=datetime.fromisoformat(entry.data[CONF_TOKEN_EXPIRES_AT]),
        refresh_after=datetime.fromisoformat(entry.data[CONF_REFRESH_AFTER]),
        session=async_get_clientsession(hass),
        on_tokens_updated=on_tokens_updated,
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

    # TEMPORARY: the previous backfill completed (flag set true) but its
    # data silently never landed in the recorder (suspected duplicate-
    # timestamp collision from adjacent-month boundary overlap, now
    # fixed). Force one more clean re-backfill to confirm. Remove after.
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STATS_BACKFILLED: False}
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
