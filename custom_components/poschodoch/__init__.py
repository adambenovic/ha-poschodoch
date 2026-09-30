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

        # TEMPORARY: verify via our own exact code path (bypassing any
        # websocket-API-specific query quirk) whether heating data really
        # landed or not.
        from homeassistant.components.recorder.statistics import get_last_statistics

        for stat_id in (
            "poschodoch:cold_water_daily",
            "poschodoch:hot_water_daily",
            "poschodoch:heating_daily_spalna",
            "poschodoch:heating_daily_kuchyna",
            "poschodoch:heating_daily_detska_izba",
            "poschodoch:heating_daily_obyvacia_izba",
        ):
            try:
                result = await hass.async_add_executor_job(
                    get_last_statistics, hass, 3, stat_id, True, {"sum", "state", "start"}
                )
                _LOGGER.warning("DIAGNOSTIC get_last_statistics(%s) = %s", stat_id, result)
            except Exception:  # pylint: disable=broad-except
                _LOGGER.warning("DIAGNOSTIC get_last_statistics(%s) FAILED", stat_id, exc_info=True)

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
