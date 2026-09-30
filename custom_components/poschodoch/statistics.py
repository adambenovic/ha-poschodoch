"""Long-term statistics backfill/sync for water and heating consumption.

poschodoch.sk's daily-consumption sensors only ever show the latest
reading; the API itself has years of history available (confirmed live:
water back to at least 2010) via Flat/DailyConsumption's year+month
params. This imports that history into Home Assistant's long-term
statistics store (Settings -> Statistics / Energy dashboard graphs) —
separate from the existing sensors, which are untouched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .const import CONF_STATS_BACKFILLED, DOMAIN

try:
    # Added in a later HA release than our pinned dev/test dependency
    # (2025.1.4) — has_mean is deprecated in favor of this on newer HA,
    # confirmed live against 2026.9.4. Degrade gracefully on either.
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:
    StatisticMeanType = None

_LOGGER = logging.getLogger(__name__)

MAX_BACKFILL_MONTHS = 700
MAX_CONSECUTIVE_MISSES = 3

WATER_SERIES = {
    "S": ("cold_water_daily", "Cold water daily consumption", "L"),
    "T": ("hot_water_daily", "Hot water daily consumption", "L"),
}


def _build_metadata(statistic_id: str, name: str, unit: str | None) -> StatisticMetaData:
    extra = {"mean_type": StatisticMeanType.NONE} if StatisticMeanType is not None else {"has_mean": False}
    return StatisticMetaData(
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
        **extra,
    )


def _entries_belong_to_month(entries: list[dict], year: int, month: int) -> bool:
    if not entries:
        return False
    prefix = f"{year:04d}-{month:02d}"
    return any(entry["date"].startswith(prefix) for entry in entries)


def _build_statistics(entries: list[dict], start_sum: float) -> list[StatisticData]:
    points: list[StatisticData] = []
    running_sum = start_sum
    for entry in entries:
        if entry["consumption"] is None:
            continue
        running_sum += entry["consumption"]
        year, month, day = (int(part) for part in entry["date"].split("-")[:3])
        points.append(
            StatisticData(
                start=datetime(year, month, day, tzinfo=timezone.utc),
                state=entry["consumption"],
                sum=running_sum,
            )
        )
    return points


def _month_before(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


async def _sweep_backward(
    fetch_month: Callable[[int, int], Awaitable[dict[str, list[dict]]]],
    start_year: int,
    start_month: int,
    max_months: int = MAX_BACKFILL_MONTHS,
) -> dict[str, list[dict]]:
    """Walk backward month by month, merging each series until several
    consecutive months' data no longer actually belongs to that month
    (either genuinely empty, or — as seen with Object/RepairFund — the
    server echoing back an unrelated period instead).

    Confirmed live: stopping at the very first miss is too fragile — a
    real account showed one isolated bad month (cause unconfirmed, maybe
    a transient backend hiccup) right in the middle of years of otherwise
    real history, and a single-miss stop silently truncated everything
    before it. Tolerating a short run of misses avoids that without
    materially risking a false continuation past the genuine start,
    since real "no more history" shows up as a long run, not one month."""
    merged: dict[str, list[dict]] = {}
    year, month = start_year, start_month
    months_walked = 0
    consecutive_misses = 0
    for _ in range(max_months):
        by_series = await fetch_month(year, month)
        if any(_entries_belong_to_month(entries, year, month) for entries in by_series.values()):
            consecutive_misses = 0
            for key, entries in by_series.items():
                merged.setdefault(key, []).extend(entries)
            months_walked += 1
            if months_walked % 12 == 0:
                _LOGGER.debug(
                    "Statistics backfill: walked back to %04d-%02d (%d months so far)",
                    year,
                    month,
                    months_walked,
                )
        else:
            consecutive_misses += 1
            if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                break
        year, month = _month_before(year, month)

    _LOGGER.debug(
        "Statistics backfill: finished sweep at %04d-%02d, %d months found",
        year,
        month,
        months_walked,
    )

    for entries in merged.values():
        entries.sort(key=lambda e: e["date"])
    return merged


async def async_backfill(hass: HomeAssistant, entry, client) -> None:
    """One-time full history import. Safe to call on every setup — skips
    immediately if already done."""
    if entry.data.get(CONF_STATS_BACKFILLED):
        return

    now = datetime.now(timezone.utc)

    water_history = await _sweep_backward(
        client.get_daily_consumption, now.year, now.month
    )
    for code, (slug, name, unit) in WATER_SERIES.items():
        entries = water_history.get(code, [])
        if not entries:
            continue
        statistic_id = f"{DOMAIN}:{slug}"
        metadata = _build_metadata(statistic_id, name, unit)
        async_add_external_statistics(hass, metadata, _build_statistics(entries, 0.0))

    heating_history = await _sweep_backward(
        client.get_heating_daily_consumption, now.year, now.month
    )
    for room, entries in heating_history.items():
        if not entries:
            continue
        statistic_id = f"{DOMAIN}:heating_daily_{slugify(room)}"
        metadata = _build_metadata(
            statistic_id, f"Heating daily consumption - {room}", None
        )
        async_add_external_statistics(hass, metadata, _build_statistics(entries, 0.0))

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STATS_BACKFILLED: True}
    )


async def _last_statistic(hass: HomeAssistant, statistic_id: str) -> tuple[datetime, float] | None:
    """get_last_statistics does blocking database I/O — must go through
    the executor, never called directly from the event loop.

    Confirmed live: rows' "start" is a float Unix timestamp (seconds),
    not a datetime — StatisticsRow's actual, longstanding shape."""
    result = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum", "start"}
    )
    rows = result.get(statistic_id)
    if not rows:
        return None
    return datetime.fromtimestamp(rows[0]["start"], tz=timezone.utc), rows[0]["sum"]


async def _sync_one_series(
    hass: HomeAssistant, statistic_id: str, name: str, unit: str | None, entries: list[dict]
) -> None:
    last = await _last_statistic(hass, statistic_id)
    last_start, start_sum = last if last is not None else (None, 0.0)

    new_entries = [
        e
        for e in sorted(entries, key=lambda e: e["date"])
        if last_start is None
        or datetime(
            *(int(p) for p in e["date"].split("-")[:3]), tzinfo=timezone.utc
        )
        > last_start
    ]
    if not new_entries:
        return

    points = _build_statistics(new_entries, start_sum)
    if not points:
        return

    metadata = _build_metadata(statistic_id, name, unit)
    async_add_external_statistics(hass, metadata, points)


async def async_sync_latest(
    hass: HomeAssistant,
    daily_consumption: dict[str, list[dict]],
    heating_daily_consumption: dict[str, list[dict]],
) -> None:
    """Called every coordinator poll with the data it already fetched for
    the current period — imports whatever days aren't in long-term
    statistics yet. Only catches up within that period; a gap spanning a
    full missed month is not backfilled here (that's what async_backfill
    is for)."""
    for code, (slug, name, unit) in WATER_SERIES.items():
        entries = daily_consumption.get(code, [])
        if entries:
            await _sync_one_series(hass, f"{DOMAIN}:{slug}", name, unit, entries)

    for room, entries in heating_daily_consumption.items():
        if entries:
            await _sync_one_series(
                hass,
                f"{DOMAIN}:heating_daily_{slugify(room)}",
                f"Heating daily consumption - {room}",
                None,
                entries,
            )
