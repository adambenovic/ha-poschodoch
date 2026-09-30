"""Long-term statistics backfill/sync for water and heating consumption.

poschodoch.sk's daily-consumption sensors only ever show the latest
reading; the API itself has years of history available via
Flat/DailyConsumption's year+month params, though usable metered data
only goes back to around mid-2023 in practice — older periods return
correctly-dated placeholder entries with consumption always null (see
_entries_belong_to_month). This imports that history into Home
Assistant's long-term statistics store (Settings -> Statistics / Energy
dashboard graphs) — separate from the existing sensors, which are
untouched.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from homeassistant.components.recorder.models import StatisticData, StatisticMetaData
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistic_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import slugify

from .api import PoschodochAuthError
from .const import CONF_STATS_BACKFILLED, DOMAIN

try:
    # Added in a later HA release than our pinned dev/test dependency
    # (2025.1.4) — has_mean is deprecated in favor of this on newer HA,
    # confirmed live against 2026.9.4. Degrade gracefully on either.
    from homeassistant.components.recorder.models import StatisticMeanType
except ImportError:
    StatisticMeanType = None

# Same deprecation pattern as mean_type (also confirmed live against
# 2026.9.4), but there's no importable symbol to probe for this one —
# TypedDict introspection instead.
_METADATA_SUPPORTS_UNIT_CLASS = "unit_class" in StatisticMetaData.__annotations__

_UNIT_CLASS_BY_UNIT = {"L": "volume"}

_LOGGER = logging.getLogger(__name__)

MAX_BACKFILL_MONTHS = 700
MAX_CONSECUTIVE_MISSES = 3
ROLLING_AVERAGE_DAYS = 30

WATER_SERIES = {
    "S": ("cold_water_daily", "Cold water daily consumption", "L"),
    "T": ("hot_water_daily", "Hot water daily consumption", "L"),
}


def _build_metadata(statistic_id: str, name: str, unit: str | None) -> StatisticMetaData:
    extra = {"mean_type": StatisticMeanType.NONE} if StatisticMeanType is not None else {"has_mean": False}
    if _METADATA_SUPPORTS_UNIT_CLASS:
        extra["unit_class"] = _UNIT_CLASS_BY_UNIT.get(unit)
    return StatisticMetaData(
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_of_measurement=unit,
        **extra,
    )


def _entries_belong_to_month(entries: list[dict], year: int, month: int) -> bool:
    """A date match alone isn't enough — confirmed live that sufficiently
    old periods return correctly-dated placeholder entries with
    consumption always null (the date scaffolding predates any usable
    metered data). Require at least one entry with real data too, so the
    sweep stops at the true edge of usable history, not the edge of the
    date scaffolding."""
    if not entries:
        return False
    prefix = f"{year:04d}-{month:02d}"
    return any(
        entry["date"].startswith(prefix) and entry["consumption"] is not None
        for entry in entries
    )


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


def _iter_water_and_heating_series(
    water_by_code: dict[str, list[dict]], heating_by_room: dict[str, list[dict]]
):
    """(statistic_id, name, unit, entries) for every water and heating
    series — the shared shape async_backfill and async_sync_latest both
    otherwise separately duplicate."""
    for code, (slug, name, unit) in WATER_SERIES.items():
        yield f"{DOMAIN}:{slug}", name, unit, water_by_code.get(code, [])
    for room, entries in heating_by_room.items():
        yield (
            f"{DOMAIN}:heating_daily_{slugify(room)}",
            f"Heating daily consumption - {room}",
            None,
            entries,
        )


async def _sweep_backward(
    fetch_month: Callable[[int, int], Awaitable[dict[str, list[dict]]]],
    start_year: int,
    start_month: int,
    max_months: int = MAX_BACKFILL_MONTHS,
    label: str = "",
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
    calls_made = 0
    consecutive_misses = 0
    for _ in range(max_months):
        try:
            by_series = await fetch_month(year, month)
        except PoschodochAuthError:
            # A real auth failure is not "just another miss" — swallowing
            # it here would let async_backfill mark the whole sweep
            # complete even though it was cut short, permanently
            # truncating long-term stats with no recovery path.
            raise
        except Exception:  # pylint: disable=broad-except
            # Confirmed live: a sufficiently old month can return a
            # non-JSON body, raising inside the request layer. Treat a
            # failed fetch the same as a data mismatch — one bad month
            # must not blow up a sweep that's already found real history.
            _LOGGER.debug(
                "Statistics backfill [%s]: fetch failed for %04d-%02d, treating as a miss",
                label,
                year,
                month,
                exc_info=True,
            )
            by_series = {}
        calls_made += 1
        hit = any(_entries_belong_to_month(entries, year, month) for entries in by_series.values())
        if calls_made % 6 == 0:
            _LOGGER.debug(
                "Statistics backfill progress [%s]: checked %04d-%02d (%s), "
                "%d calls made, %d months kept",
                label,
                year,
                month,
                "hit" if hit else "miss",
                calls_made,
                months_walked,
            )
        if hit:
            consecutive_misses = 0
            for key, entries in by_series.items():
                merged.setdefault(key, []).extend(entries)
            months_walked += 1
        else:
            consecutive_misses += 1
            if consecutive_misses >= MAX_CONSECUTIVE_MISSES:
                break
        year, month = _month_before(year, month)

    _LOGGER.debug(
        "Statistics backfill [%s]: finished sweep at %04d-%02d, %d months found in %d calls",
        label,
        year,
        month,
        months_walked,
        calls_made,
    )

    for key, entries in merged.items():
        # Adjacent months' independent API calls can both include the same
        # boundary day — keep the last-seen value for any duplicate date
        # rather than passing duplicate timestamps into the statistics
        # import.
        by_date = {e["date"]: e for e in entries}
        merged[key] = sorted(by_date.values(), key=lambda e: e["date"])
    return merged


async def async_backfill(hass: HomeAssistant, entry, client) -> None:
    """One-time full history import. Safe to call on every setup — skips
    immediately if already done."""
    if entry.data.get(CONF_STATS_BACKFILLED):
        return

    now = datetime.now(timezone.utc)

    # Sequential, not parallel: both sweeps share this same client, whose
    # token-refresh logic isn't lock-protected — concurrent sweeps could
    # race and rotate the token out from under each other.
    water_history = await _sweep_backward(
        client.get_daily_consumption, now.year, now.month, label="water"
    )
    heating_history = await _sweep_backward(
        client.get_heating_daily_consumption, now.year, now.month, label="heating"
    )

    for statistic_id, name, unit, entries in _iter_water_and_heating_series(
        water_history, heating_history
    ):
        if not entries:
            continue
        metadata = _build_metadata(statistic_id, name, unit)
        points = _build_statistics(entries, 0.0)
        _LOGGER.info(
            "Statistics backfill: submitting %d points for %s (%s..%s)",
            len(points),
            statistic_id,
            points[0]["start"] if points else None,
            points[-1]["start"] if points else None,
        )
        async_add_external_statistics(hass, metadata, points)

    hass.config_entries.async_update_entry(
        entry, data={**entry.data, CONF_STATS_BACKFILLED: True}
    )


async def _last_statistic(hass: HomeAssistant, statistic_id: str) -> tuple[datetime, float] | None:
    """get_last_statistics does blocking database I/O — must go through
    the executor, never called directly from the event loop.

    Confirmed against HA source: "start" isn't a valid member of the
    types param (only last_reset/max/mean/min/state/sum are) — every
    returned row carries "start" regardless of what's requested, but
    passing it anyway defeats the recorder's sum-only fast path for no
    benefit. Confirmed live: rows' "start" is a float Unix timestamp
    (seconds), not a datetime — StatisticsRow's actual, longstanding
    shape."""
    result = await hass.async_add_executor_job(
        get_last_statistics, hass, 1, statistic_id, True, {"sum"}
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
    for statistic_id, name, unit, entries in _iter_water_and_heating_series(
        daily_consumption, heating_daily_consumption
    ):
        if entries:
            await _sync_one_series(hass, statistic_id, name, unit, entries)


async def get_rolling_average(
    hass: HomeAssistant, statistic_id: str, days: int = ROLLING_AVERAGE_DAYS
) -> float | None:
    """Average per day over the trailing window, computed from the
    recorder's own period-total ("change") rather than "mean" — this is a
    has_sum-only series (no per-point mean stored), and "mean" reads back
    as null for that kind of series. statistic_during_period does
    blocking database I/O, same as get_last_statistics — must go through
    the executor."""
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    result = await hass.async_add_executor_job(
        statistic_during_period, hass, start, now, statistic_id, {"change"}, None
    )
    change = result.get("change")
    if change is None:
        return None
    return change / days


async def get_rolling_averages(
    hass: HomeAssistant, heating_rooms: list[str], days: int = ROLLING_AVERAGE_DAYS
) -> dict[str, float | None]:
    """Rolling daily averages for every water and heating series, keyed
    the same way the coordinator's own daily_consumption/
    heating_daily_consumption dicts are ("S"/"T" for water, room name for
    heating) so sensors can look themselves up directly."""
    averages: dict[str, float | None] = {}
    for code, (slug, _name, _unit) in WATER_SERIES.items():
        averages[code] = await get_rolling_average(hass, f"{DOMAIN}:{slug}", days)
    for room in heating_rooms:
        averages[room] = await get_rolling_average(
            hass, f"{DOMAIN}:heating_daily_{slugify(room)}", days
        )
    return averages
