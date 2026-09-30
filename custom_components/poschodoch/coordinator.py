"""Data update coordinator for poschodoch.sk."""
from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import statistics
from .api import PoschodochApiClient, PoschodochAuthError
from .const import DEFAULT_SCAN_INTERVAL_HOURS, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONSUMPTION_TYPES = ("S", "T", "U")


class PoschodochDataUpdateCoordinator(DataUpdateCoordinator):
    """Fetches all poschodoch.sk data on one schedule."""

    def __init__(self, hass: HomeAssistant, client: PoschodochApiClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(hours=DEFAULT_SCAN_INTERVAL_HOURS),
        )
        self.client = client

    async def _async_update_data(self) -> dict:
        try:
            daily_consumption = await self.client.get_daily_consumption()
            heating_daily_consumption = await self.client.get_heating_daily_consumption()
            consumption_status = {
                type_code: await self.client.get_consumption_status(type_code)
                for type_code in CONSUMPTION_TYPES
            }
            meter_readings = await self.client.get_meter_readings()
            account = await self.client.get_account()
            repair_fund = await self.client.get_repair_fund()
        except PoschodochAuthError as err:
            raise ConfigEntryAuthFailed from err
        except Exception as err:  # pylint: disable=broad-except
            # Home Assistant's own ConfigEntryNotReady/UpdateFailed handling
            # only ever surfaces str(err) to the user — no traceback — so
            # without logging it here ourselves, an unexpected bug is
            # completely undiagnosable from the logs alone.
            _LOGGER.exception("Unexpected error fetching poschodoch.sk data")
            raise UpdateFailed(str(err)) from err

        try:
            await statistics.async_sync_latest(
                self.hass, daily_consumption, heating_daily_consumption
            )
        except Exception:  # pylint: disable=broad-except
            # Long-term statistics are supplementary — a failure here must
            # never take down the live sensors.
            _LOGGER.warning(
                "Failed to sync long-term statistics for poschodoch.sk data",
                exc_info=True,
            )

        try:
            rolling_averages = await statistics.get_rolling_averages(
                self.hass, list(heating_daily_consumption)
            )
        except Exception:  # pylint: disable=broad-except
            # Supplementary, same contract as the statistics sync above.
            _LOGGER.warning(
                "Failed to compute rolling averages for poschodoch.sk data",
                exc_info=True,
            )
            rolling_averages = {}

        return {
            "daily_consumption": daily_consumption,
            "heating_daily_consumption": heating_daily_consumption,
            "consumption_status": consumption_status,
            "meter_readings": meter_readings,
            "account": account,
            "repair_fund": repair_fund,
            "rolling_averages": rolling_averages,
        }
