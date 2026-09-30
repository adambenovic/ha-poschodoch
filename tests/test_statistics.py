from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.poschodoch import statistics as stats
from custom_components.poschodoch.const import CONF_STATS_BACKFILLED, DOMAIN


def test_build_metadata_includes_mean_type_when_available():
    """StatisticMetaData in newer HA versions requires mean_type (has_mean
    is deprecated) — confirmed live against HA 2026.9.4, which our pinned
    dev/test HA version (2025.1.4) predates and doesn't expose at all, so
    this must degrade gracefully rather than import-erroring on either."""
    metadata = stats._build_metadata("poschodoch:cold_water_daily", "Cold water", "L")

    assert metadata["has_sum"] is True
    assert metadata["source"] == DOMAIN
    assert metadata["statistic_id"] == "poschodoch:cold_water_daily"
    assert metadata["unit_of_measurement"] == "L"
    if stats.StatisticMeanType is not None:
        assert metadata["mean_type"] == stats.StatisticMeanType.NONE
    else:
        assert metadata["has_mean"] is False


def test_entries_belong_to_month_true_when_dates_match():
    entries = [{"date": "2022-03-01", "consumption": 1.0}]
    assert stats._entries_belong_to_month(entries, 2022, 3) is True


def test_entries_belong_to_month_false_when_dates_dont_match():
    """Guards against the same kind of backend quirk found with
    Object/RepairFund, where out-of-range periods echoed the current
    period back instead of returning empty."""
    entries = [{"date": "2026-09-21", "consumption": 1.0}]
    assert stats._entries_belong_to_month(entries, 2022, 3) is False


def test_entries_belong_to_month_false_when_empty():
    assert stats._entries_belong_to_month([], 2022, 3) is False


def test_build_statistics_computes_cumulative_sum():
    entries = [
        {"date": "2022-03-01", "consumption": 10.0},
        {"date": "2022-03-02", "consumption": 5.0},
    ]

    points = stats._build_statistics(entries, start_sum=0.0)

    assert [p["sum"] for p in points] == [10.0, 15.0]
    assert [p["state"] for p in points] == [10.0, 5.0]
    assert points[0]["start"] == datetime(2022, 3, 1, tzinfo=timezone.utc)


def test_build_statistics_continues_from_starting_sum():
    entries = [{"date": "2022-04-01", "consumption": 5.0}]

    points = stats._build_statistics(entries, start_sum=100.0)

    assert points[0]["sum"] == 105.0


def test_build_statistics_skips_null_consumption_days():
    entries = [
        {"date": "2022-03-01", "consumption": 10.0},
        {"date": "2022-03-02", "consumption": None},
        {"date": "2022-03-03", "consumption": 5.0},
    ]

    points = stats._build_statistics(entries, start_sum=0.0)

    assert [p["start"].day for p in points] == [1, 3]
    assert [p["sum"] for p in points] == [10.0, 15.0]


@pytest.mark.asyncio
async def test_sweep_backward_merges_months_until_empty():
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": [{"date": "2026-08-01", "consumption": 2.0}]},
        (2026, 7): {"S": []},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == ["2026-08-01", "2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_stops_when_month_doesnt_belong():
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == ["2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_respects_max_months_cap():
    calls = []

    async def fetch_month(year, month):
        calls.append((year, month))
        return {"S": [{"date": f"{year:04d}-{month:02d}-01", "consumption": 1.0}]}

    await stats._sweep_backward(fetch_month, 2026, 9, max_months=3)

    assert len(calls) == 3


@pytest.mark.asyncio
async def test_async_backfill_imports_water_and_heating_and_sets_flag(hass, freezer):
    freezer.move_to("2026-09-29")
    client = AsyncMock()
    client.get_daily_consumption.side_effect = [
        {"S": [{"date": "2026-09-01", "consumption": 10.0}], "T": [{"date": "2026-09-01", "consumption": 5.0}]},
        {"S": [], "T": []},
    ]
    client.get_heating_daily_consumption.side_effect = [
        {"Kuchyňa": [{"date": "2026-09-01", "consumption": 1.0}]},
        {},
    ]

    entry = type("Entry", (), {"data": {}, "entry_id": "e1"})()

    with patch.object(hass.config_entries, "async_update_entry") as mock_update, patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_backfill(hass, entry, client)

    assert mock_add_stats.call_count == 3
    imported_ids = {call.args[1]["statistic_id"] for call in mock_add_stats.call_args_list}
    assert imported_ids == {
        f"{DOMAIN}:cold_water_daily",
        f"{DOMAIN}:hot_water_daily",
        f"{DOMAIN}:heating_daily_kuchyna",
    }
    mock_update.assert_called_once()
    assert mock_update.call_args.kwargs["data"][CONF_STATS_BACKFILLED] is True


@pytest.mark.asyncio
async def test_async_backfill_skips_if_already_backfilled(hass):
    client = AsyncMock()
    entry = type("Entry", (), {"data": {CONF_STATS_BACKFILLED: True}, "entry_id": "e1"})()

    with patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_backfill(hass, entry, client)

    mock_add_stats.assert_not_called()
    client.get_daily_consumption.assert_not_called()


@pytest.mark.asyncio
async def test_async_sync_latest_reads_last_statistic_via_executor(hass):
    """get_last_statistics does blocking database I/O — confirmed live
    that HA's recorder raises RuntimeError if it's called directly from
    the event loop instead of via hass.async_add_executor_job."""
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": 10.0}]}

    async def fake_executor_job(func, *args):
        return func(*args)

    with patch(
        "custom_components.poschodoch.statistics.get_last_statistics",
        return_value={},
    ) as mock_get_last, patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ), patch.object(
        hass, "async_add_executor_job", side_effect=fake_executor_job
    ) as mock_executor_job:
        await stats.async_sync_latest(hass, daily_consumption, {})

    mock_executor_job.assert_called_once_with(mock_get_last, hass, 1, f"{DOMAIN}:cold_water_daily", True, {"sum", "start"})


@pytest.mark.asyncio
async def test_async_sync_latest_imports_only_new_entries(hass):
    daily_consumption = {
        "S": [
            {"date": "2026-09-24", "consumption": 10.0},
            {"date": "2026-09-25", "consumption": 5.0},
        ]
    }

    def fake_last_stats(hass_, n, statistic_id, convert_units, types):
        return {
            statistic_id: [
                {"start": datetime(2026, 9, 24, tzinfo=timezone.utc).timestamp(), "sum": 10.0}
            ]
        }

    with patch(
        "custom_components.poschodoch.statistics.get_last_statistics",
        side_effect=fake_last_stats,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    assert mock_add_stats.call_count == 1
    _, metadata, points = mock_add_stats.call_args.args
    assert metadata["statistic_id"] == f"{DOMAIN}:cold_water_daily"
    points = list(points)
    assert len(points) == 1
    assert points[0]["start"] == datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert points[0]["sum"] == 15.0


@pytest.mark.asyncio
async def test_async_sync_latest_does_nothing_when_no_new_entries(hass):
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": 10.0}]}

    def fake_last_stats(hass_, n, statistic_id, convert_units, types):
        return {
            statistic_id: [
                {"start": datetime(2026, 9, 24, tzinfo=timezone.utc).timestamp(), "sum": 10.0}
            ]
        }

    with patch(
        "custom_components.poschodoch.statistics.get_last_statistics",
        side_effect=fake_last_stats,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    mock_add_stats.assert_not_called()


@pytest.mark.asyncio
async def test_async_sync_latest_starts_from_zero_when_no_prior_statistics(hass):
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": 10.0}]}

    def fake_last_stats(hass_, n, statistic_id, convert_units, types):
        return {}

    with patch(
        "custom_components.poschodoch.statistics.get_last_statistics",
        side_effect=fake_last_stats,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    _, _, points = mock_add_stats.call_args.args
    points = list(points)
    assert points[0]["sum"] == 10.0
