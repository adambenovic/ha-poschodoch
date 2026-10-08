from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.poschodoch import statistics as stats
from custom_components.poschodoch.api import PoschodochAuthError
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


def test_build_metadata_includes_unit_class_for_volume_when_supported():
    """Same deprecation pattern as mean_type, confirmed live against HA
    2026.9.4 — but there's no importable symbol to probe for this one, so
    availability is checked via TypedDict introspection instead."""
    metadata = stats._build_metadata("poschodoch:cold_water_daily", "Cold water", "L")

    if stats._METADATA_SUPPORTS_UNIT_CLASS:
        assert metadata["unit_class"] == "volume"
    else:
        assert "unit_class" not in metadata


def test_build_metadata_omits_unit_class_for_unitless_series():
    metadata = stats._build_metadata("poschodoch:heating_daily_kuchyna", "Heating", None)

    if stats._METADATA_SUPPORTS_UNIT_CLASS:
        assert metadata["unit_class"] is None
    else:
        assert "unit_class" not in metadata


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


def test_entries_belong_to_month_false_when_all_consumption_is_null():
    """Confirmed live: for sufficiently old periods the API returns
    correctly-dated placeholder entries with consumption always null —
    the date scaffolding exists long before any usable metered data does.
    A date-only match isn't real history; it just walks the sweep
    through years of unusable null placeholders."""
    entries = [
        {"date": "2008-06-01", "consumption": None},
        {"date": "2008-06-02", "consumption": None},
    ]
    assert stats._entries_belong_to_month(entries, 2008, 6) is False


def test_entries_belong_to_month_true_when_some_consumption_is_real():
    entries = [
        {"date": "2022-03-01", "consumption": None},
        {"date": "2022-03-02", "consumption": 1.5},
    ]
    assert stats._entries_belong_to_month(entries, 2022, 3) is True


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
async def test_sweep_backward_deduplicates_boundary_day_across_adjacent_months():
    """Suspected live bug: adjacent months' independent API calls can both
    include the same boundary day, producing duplicate (date, room)
    entries once merged. The recorder's per-row statistics insert appears
    to silently drop an entire import when it hits a same-timestamp
    collision it doesn't like, which matches heating series ending up
    with zero rows despite the sweep reporting real months found."""
    responses = {
        (2026, 9): {
            "S": [
                {"date": "2026-09-01", "consumption": 1.0},
                {"date": "2026-08-31", "consumption": 99.0},  # boundary overlap
            ]
        },
        (2026, 8): {
            "S": [
                {"date": "2026-08-31", "consumption": 2.0},
                {"date": "2026-08-01", "consumption": 3.0},
            ]
        },
        (2026, 7): {"S": []},
        (2026, 6): {"S": []},
        (2026, 5): {"S": []},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    dates = [e["date"] for e in result["S"]]
    assert dates == ["2026-08-01", "2026-08-31", "2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_merges_months_until_consecutive_misses():
    """Confirmed live: a real account's history can have an isolated
    flaky/empty month in the middle of otherwise-real data (cause
    unconfirmed — possibly a transient backend hiccup) — stopping at the
    very first miss silently truncated years of real history. Requires
    several misses in a row before concluding history has ended."""
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": [{"date": "2026-08-01", "consumption": 2.0}]},
        (2026, 7): {"S": []},
        (2026, 6): {"S": []},
        (2026, 5): {"S": []},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == ["2026-08-01", "2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_stops_after_consecutive_mismatched_months():
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 7): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 6): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == ["2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_tolerates_an_isolated_miss():
    """Regression test for the live bug: one bad month in the middle of a
    real, continuous history must not truncate everything before it."""
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": []},  # isolated miss
        (2026, 7): {"S": [{"date": "2026-07-01", "consumption": 3.0}]},
        (2026, 6): {"S": [{"date": "2026-06-01", "consumption": 4.0}]},
        (2026, 5): {"S": []},
        (2026, 4): {"S": []},
        (2026, 3): {"S": []},
    }

    async def fetch_month(year, month):
        return responses[(year, month)]

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == [
        "2026-06-01",
        "2026-07-01",
        "2026-09-01",
    ]


@pytest.mark.asyncio
async def test_sweep_backward_treats_fetch_errors_as_a_miss():
    """Regression test for the live bug: for a sufficiently old month the
    real API returned a non-JSON body, raising inside the request layer
    and crashing the whole sweep — losing 200+ months of already-found
    real history since nothing is written until the sweep completes. A
    per-month fetch failure must be tolerated exactly like a data
    mismatch, not left to blow up the entire operation."""
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
        (2026, 8): {"S": [{"date": "2026-08-01", "consumption": 2.0}]},
    }

    async def fetch_month(year, month):
        if (year, month) in responses:
            return responses[(year, month)]
        raise ValueError("unexpected character: line 1 column 1 (char 0)")

    result = await stats._sweep_backward(fetch_month, 2026, 9)

    assert [e["date"] for e in result["S"]] == ["2026-08-01", "2026-09-01"]


@pytest.mark.asyncio
async def test_sweep_backward_lets_auth_errors_propagate():
    """A real PoschodochAuthError (refresh token rejected mid-sweep) must
    not be treated as just another miss — that would let async_backfill
    mark CONF_STATS_BACKFILLED=True even though the sweep was cut short by
    an auth failure, permanently truncating long-term stats with no
    recovery path. Only genuine data/parse failures are tolerated misses."""
    responses = {
        (2026, 9): {"S": [{"date": "2026-09-01", "consumption": 1.0}]},
    }

    async def fetch_month(year, month):
        if (year, month) in responses:
            return responses[(year, month)]
        raise PoschodochAuthError("Refresh token rejected")

    with pytest.raises(PoschodochAuthError):
        await stats._sweep_backward(fetch_month, 2026, 9)


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
        {"S": [], "T": []},
        {"S": [], "T": []},
    ]
    client.get_heating_daily_consumption.side_effect = [
        {"Kuchyňa": [{"date": "2026-09-01", "consumption": 1.0}]},
        {},
        {},
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
async def test_async_sync_latest_reads_anchor_sum_via_executor(hass):
    """statistics_during_period does blocking database I/O — confirmed
    live that HA's recorder raises RuntimeError if it's called directly
    from the event loop instead of via hass.async_add_executor_job."""
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": 10.0}]}

    async def fake_executor_job(func, *args):
        return func(*args)

    with patch(
        "custom_components.poschodoch.statistics.statistics_during_period",
        return_value={},
    ) as mock_stats_during_period, patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ), patch.object(
        hass, "async_add_executor_job", side_effect=fake_executor_job
    ) as mock_executor_job:
        await stats.async_sync_latest(hass, daily_consumption, {})

    mock_executor_job.assert_called_once_with(
        mock_stats_during_period,
        hass,
        datetime.min.replace(tzinfo=timezone.utc),
        datetime(2026, 9, 24, tzinfo=timezone.utc),
        {f"{DOMAIN}:cold_water_daily"},
        "day",
        None,
        {"sum"},
    )


@pytest.mark.asyncio
async def test_async_sync_latest_recomputes_cumulative_sum_from_anchor(hass):
    daily_consumption = {
        "S": [
            {"date": "2026-09-24", "consumption": 10.0},
            {"date": "2026-09-25", "consumption": 5.0},
        ]
    }

    def fake_stats_during_period(hass_, start, end, statistic_ids, period, units, types):
        statistic_id = next(iter(statistic_ids))
        return {
            statistic_id: [
                {"start": datetime(2026, 9, 23, tzinfo=timezone.utc).timestamp(), "sum": 100.0}
            ]
        }

    with patch(
        "custom_components.poschodoch.statistics.statistics_during_period",
        side_effect=fake_stats_during_period,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    assert mock_add_stats.call_count == 1
    _, metadata, points = mock_add_stats.call_args.args
    assert metadata["statistic_id"] == f"{DOMAIN}:cold_water_daily"
    points = list(points)
    assert len(points) == 2
    assert points[0]["start"] == datetime(2026, 9, 24, tzinfo=timezone.utc)
    assert points[0]["sum"] == 110.0
    assert points[1]["start"] == datetime(2026, 9, 25, tzinfo=timezone.utc)
    assert points[1]["sum"] == 115.0


@pytest.mark.asyncio
async def test_async_sync_latest_fills_a_hole_left_by_a_previously_null_day(hass):
    """Regression test for the live bug: poschodoch.sk finalized Sep 24's
    consumption only after Sep 30 already had a real value recorded,
    because Sep 24 was still null on the poll that happened to run
    first. Resyncing the whole window (anchored before Sep 24, not just
    "after the latest recorded date") must fill that hole in and shift
    everything after it to the now-correct running total."""
    daily_consumption = {
        "S": [
            {"date": "2026-09-23", "consumption": 290.0},  # already recorded
            {"date": "2026-09-24", "consumption": 124.0},  # the hole, now real
            {"date": "2026-09-30", "consumption": 297.0},  # already recorded (wrongly, as if it were the day right after the 23rd)
        ]
    }

    def fake_stats_during_period(hass_, start, end, statistic_ids, period, units, types):
        statistic_id = next(iter(statistic_ids))
        return {
            statistic_id: [
                {"start": datetime(2026, 9, 22, tzinfo=timezone.utc).timestamp(), "sum": 1000.0}
            ]
        }

    with patch(
        "custom_components.poschodoch.statistics.statistics_during_period",
        side_effect=fake_stats_during_period,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    _, _, points = mock_add_stats.call_args.args
    points = list(points)
    assert [p["start"].day for p in points] == [23, 24, 30]
    assert [p["sum"] for p in points] == [1290.0, 1414.0, 1711.0]


@pytest.mark.asyncio
async def test_async_sync_latest_starts_from_zero_when_no_prior_statistics(hass):
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": 10.0}]}

    def fake_stats_during_period(hass_, start, end, statistic_ids, period, units, types):
        return {}

    with patch(
        "custom_components.poschodoch.statistics.statistics_during_period",
        side_effect=fake_stats_during_period,
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    _, _, points = mock_add_stats.call_args.args
    points = list(points)
    assert points[0]["sum"] == 10.0


@pytest.mark.asyncio
async def test_async_sync_latest_does_nothing_when_all_entries_are_null(hass):
    """The one truly no-op case left: a future, not-yet-billed day with
    no real data at all — _build_statistics already skips null entries,
    so there's nothing to submit."""
    daily_consumption = {"S": [{"date": "2026-09-24", "consumption": None}]}

    with patch(
        "custom_components.poschodoch.statistics.statistics_during_period",
        return_value={},
    ), patch(
        "custom_components.poschodoch.statistics.async_add_external_statistics"
    ) as mock_add_stats:
        await stats.async_sync_latest(hass, daily_consumption, {})

    mock_add_stats.assert_not_called()


@pytest.mark.asyncio
async def test_get_rolling_average_divides_period_change_by_days(hass):
    """Long-term stats only store state+sum (no per-point mean, since this
    is a has_sum-only series) — confirmed live that querying "mean"
    returns null for a series like ours. "change" (the recorder's own
    period-total computation) divided by the window length is the
    correct way to get a genuine average-per-day figure."""

    def fake_statistic_during_period(hass_, start, end, statistic_id, types, units):
        assert types == {"change"}
        assert (end - start).days == 30
        return {"change": 300.0}

    with patch(
        "custom_components.poschodoch.statistics.statistic_during_period",
        side_effect=fake_statistic_during_period,
    ):
        result = await stats.get_rolling_average(hass, "poschodoch:cold_water_daily")

    assert result == pytest.approx(10.0)


@pytest.mark.asyncio
async def test_get_rolling_average_runs_via_executor(hass):
    """statistic_during_period does blocking database I/O, same as
    get_last_statistics — must never be called directly from the event
    loop."""

    async def fake_executor_job(func, *args):
        return func(*args)

    with patch(
        "custom_components.poschodoch.statistics.statistic_during_period",
        return_value={"change": 30.0},
    ) as mock_stat, patch.object(
        hass, "async_add_executor_job", side_effect=fake_executor_job
    ) as mock_executor_job:
        await stats.get_rolling_average(hass, "poschodoch:cold_water_daily")

    mock_executor_job.assert_called_once()
    assert mock_executor_job.call_args.args[0] is mock_stat


@pytest.mark.asyncio
async def test_get_rolling_average_returns_none_when_change_is_none(hass):
    with patch(
        "custom_components.poschodoch.statistics.statistic_during_period",
        return_value={"change": None},
    ):
        result = await stats.get_rolling_average(hass, "poschodoch:cold_water_daily")

    assert result is None


@pytest.mark.asyncio
async def test_get_rolling_averages_covers_water_and_all_heating_rooms(hass):
    async def fake_get_rolling_average(hass_, statistic_id, days=30):
        return {
            "poschodoch:cold_water_daily": 10.0,
            "poschodoch:hot_water_daily": 2.0,
            "poschodoch:heating_daily_kuchyna": 1.5,
        }.get(statistic_id)

    with patch(
        "custom_components.poschodoch.statistics.get_rolling_average",
        side_effect=fake_get_rolling_average,
    ):
        result = await stats.get_rolling_averages(hass, ["Kuchyňa"])

    assert result == {"S": 10.0, "T": 2.0, "Kuchyňa": 1.5}
