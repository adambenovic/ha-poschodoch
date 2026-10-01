from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.poschodoch.api import PoschodochAuthError
from custom_components.poschodoch.coordinator import PoschodochDataUpdateCoordinator


def make_fake_client():
    client = AsyncMock()
    client.get_daily_consumption.return_value = {
        "S": [{"date": "2026-09-24", "consumption": 290.0}],
        "T": [{"date": "2026-09-24", "consumption": 54.0}],
    }
    client.get_consumption_status.side_effect = lambda type_code: {
        "S": {"actual_consumption": 38.28, "diff_consumption": 8.65, "percent_consumption": 29.0, "unit": "m3"},
        "T": {"actual_consumption": 10.0, "diff_consumption": 1.0, "percent_consumption": 5.0, "unit": "m3"},
        "U": {"actual_consumption": 644.0, "diff_consumption": -161.0, "percent_consumption": -20.0, "unit": "d./kWh"},
    }[type_code]
    client.get_heating_daily_consumption.return_value = {
        "Kuchyňa": [{"date": "2026-09-24", "consumption": 1.5}],
    }
    client.get_meter_readings.return_value = [
        {"meter_id": 1, "meter_number": "abc", "meter_type": "UK", "room": "Kuchyňa"},
    ]
    client.get_account.return_value = {
        "due_balance": 195.60,
        "due_date": "2026-09-30",
        "last_payment_amount": 184.24,
        "last_payment_date": "2026-09-02",
    }
    client.get_repair_fund.return_value = {
        "balance": 109.28,
        "year": 2026,
        "recent_entries": [],
    }
    return client


@pytest.mark.asyncio
async def test_coordinator_assembles_all_domains(hass):
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    await coordinator.async_refresh()

    assert coordinator.data["daily_consumption"]["S"][0]["consumption"] == 290.0
    assert coordinator.data["heating_daily_consumption"]["Kuchyňa"][0]["consumption"] == 1.5
    assert coordinator.data["consumption_status"]["S"]["percent_consumption"] == 29.0
    assert coordinator.data["consumption_status"]["U"]["percent_consumption"] == -20.0
    assert coordinator.data["meter_readings"][0]["room"] == "Kuchyňa"
    assert coordinator.data["account"]["due_balance"] == 195.60
    assert coordinator.data["repair_fund"]["balance"] == 109.28


@pytest.mark.asyncio
async def test_coordinator_raises_auth_failed_on_rejected_refresh_token(hass):
    client = make_fake_client()
    client.get_daily_consumption.side_effect = PoschodochAuthError("nope")
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    await coordinator.async_refresh()

    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)


@pytest.mark.asyncio
async def test_coordinator_raises_update_failed_on_other_errors(hass):
    client = make_fake_client()
    client.get_daily_consumption.side_effect = RuntimeError("network blip")
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    await coordinator.async_refresh()

    assert isinstance(coordinator.last_exception, UpdateFailed)


@pytest.mark.asyncio
async def test_coordinator_logs_full_traceback_on_unexpected_errors(hass, caplog):
    """Home Assistant's own UpdateFailed/ConfigEntryNotReady handling only
    surfaces str(err) to the user, with no traceback — without an explicit
    log here, an unexpected bug is undiagnosable from the logs alone."""
    import logging

    client = make_fake_client()
    client.get_daily_consumption.side_effect = TypeError(
        "float() argument must be a string or a real number, not 'NoneType'"
    )
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    with caplog.at_level(logging.ERROR, logger="custom_components.poschodoch.coordinator"):
        await coordinator.async_refresh()

    records = [
        r
        for r in caplog.records
        if r.name == "custom_components.poschodoch.coordinator"
    ]
    assert any(r.exc_info is not None for r in records), (
        "expected a log record with a full traceback (exc_info), found none"
    )


@pytest.mark.asyncio
async def test_coordinator_syncs_statistics_with_fetched_daily_data(hass):
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    with patch(
        "custom_components.poschodoch.coordinator.statistics.async_sync_latest",
        new=AsyncMock(),
    ) as mock_sync:
        await coordinator.async_refresh()

    mock_sync.assert_called_once_with(
        hass,
        client.get_daily_consumption.return_value,
        client.get_heating_daily_consumption.return_value,
    )


@pytest.mark.asyncio
async def test_coordinator_survives_statistics_sync_failure(hass):
    """Long-term statistics are supplementary — a failure there must never
    take down the live sensors."""
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    with patch(
        "custom_components.poschodoch.coordinator.statistics.async_sync_latest",
        new=AsyncMock(side_effect=RuntimeError("recorder not ready")),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_exception is None
    assert coordinator.data["account"]["due_balance"] == 195.60


@pytest.mark.asyncio
async def test_coordinator_exposes_rolling_averages(hass):
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    with patch(
        "custom_components.poschodoch.coordinator.statistics.get_rolling_averages",
        new=AsyncMock(return_value={"S": 10.0, "T": 2.0, "Kuchyňa": 1.5}),
    ) as mock_averages:
        await coordinator.async_refresh()

    mock_averages.assert_called_once_with(hass, ["Kuchyňa"])
    assert coordinator.data["rolling_averages"] == {"S": 10.0, "T": 2.0, "Kuchyňa": 1.5}


@pytest.mark.asyncio
async def test_coordinator_survives_rolling_averages_failure(hass):
    """Same resilience contract as the statistics sync — a failure here
    must never take down the live sensors."""
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    with patch(
        "custom_components.poschodoch.coordinator.statistics.get_rolling_averages",
        new=AsyncMock(side_effect=RuntimeError("recorder not ready")),
    ):
        await coordinator.async_refresh()

    assert coordinator.last_exception is None
    assert coordinator.data["rolling_averages"] == {}


@pytest.mark.asyncio
async def test_coordinator_raises_update_failed_not_auth_failed_on_transient_api_error(hass):
    """PoschodochApiError (e.g. a transient 5xx from the server) must be
    treated as a normal retryable failure, not force a reauth flow — only
    a genuine PoschodochAuthError should do that."""
    from custom_components.poschodoch.api import PoschodochApiError

    client = make_fake_client()
    client.get_daily_consumption.side_effect = PoschodochApiError("server error")
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    await coordinator.async_refresh()

    assert isinstance(coordinator.last_exception, UpdateFailed)


@pytest.mark.asyncio
async def test_coordinator_has_no_fixed_update_interval(hass):
    """Polling is scheduled explicitly at a fixed local time instead (see
    async_track_time_change in __init__.py) — a plain interval can't
    express "once a day at a specific hour."""
    client = make_fake_client()
    coordinator = PoschodochDataUpdateCoordinator(hass, client)

    assert coordinator.update_interval is None
