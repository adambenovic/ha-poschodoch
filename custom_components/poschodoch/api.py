"""API client for poschodoch.sk."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

import aiohttp

_LOGGER = logging.getLogger(__name__)

BASE_URL = "https://api.poschodoch.sk/api/"
TOKEN_LIFETIME = timedelta(hours=2)
REFRESH_MARGIN = timedelta(hours=1)
MENU_CACHE_LIFETIME = timedelta(hours=24)
REPAIR_FUND_LOOKBACK_YEARS_CAP = 60


class PoschodochAuthError(Exception):
    """Raised when the refresh token itself is rejected."""


def _entries_belong_to_year(entries: list[dict], year: int) -> bool:
    year_prefix = str(year)
    return any(entry["date"].startswith(year_prefix) for entry in entries)


def _to_float(value: str | float | None) -> float | None:
    """poschodoch.sk sends numeric fields as JSON strings, but some (e.g. a
    missed meter reading or a not-yet-billed period) legitimately come back
    as null."""
    return None if value is None else float(value)


class PoschodochApiClient:
    """Talks to the poschodoch.sk backend."""

    def __init__(
        self,
        id_token: str,
        id_refresh_token: str,
        token_expires_at: datetime,
        refresh_after: datetime,
        session: aiohttp.ClientSession | None = None,
        on_tokens_updated: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._id_token = id_token
        self._id_refresh_token = id_refresh_token
        self._token_expires_at = token_expires_at
        self._refresh_after = refresh_after
        self._owns_session = session is None
        self._session = session or aiohttp.ClientSession()
        self._on_tokens_updated = on_tokens_updated
        self._menu_map: dict[str, int] | None = None
        self._menu_map_fetched_at: datetime | None = None
        self._portal_id: int | None = None
        self._repair_fund_history: dict[int, list[dict]] | None = None

    async def get_menu_map(self) -> dict[str, int]:
        now = datetime.now(timezone.utc)
        if (
            self._menu_map is not None
            and self._menu_map_fetched_at is not None
            and now - self._menu_map_fetched_at < MENU_CACHE_LIFETIME
        ):
            return self._menu_map

        entries = await self.request("GET", "Dashboard/Menu")
        self._menu_map = {entry["MenuCode"]: entry["MenuId"] for entry in entries}
        self._menu_map_fetched_at = now
        return self._menu_map

    async def get_daily_consumption(self) -> dict[str, list[dict]]:
        menu_map = await self.get_menu_map()
        menu_id = menu_map["DailyConsumption"]
        body = await self.request(
            "GET", "Flat/DailyConsumption", params={"menuId": menu_id, "type": "S"}
        )
        partitioned: dict[str, list[dict]] = {}
        for entry in body["Consumption"]:
            partitioned.setdefault(entry["Code"], []).append(
                {"date": entry["Date"], "consumption": _to_float(entry["Consumption"])}
            )
        return partitioned

    async def get_heating_daily_consumption(self) -> dict[str, list[dict]]:
        menu_map = await self.get_menu_map()
        menu_id = menu_map["DailyConsumption"]
        body = await self.request(
            "GET", "Flat/DailyConsumption", params={"menuId": menu_id, "type": "U"}
        )
        by_room: dict[str, list[dict]] = {}
        for entry in body["Consumption"]:
            by_room.setdefault(entry["Type"], []).append(
                {"date": entry["Date"], "consumption": _to_float(entry["Consumption"])}
            )
        return by_room

    async def get_consumption_status(self, type_code: str) -> dict:
        menu_map = await self.get_menu_map()
        menu_id = menu_map["ConsumptionStatus"]
        body = await self.request(
            "GET",
            "Flat/ConsumptionStatus",
            params={"menuId": menu_id, "type": type_code},
        )
        percent_consumption = _to_float(body["PercConsumption"])
        return {
            "actual_consumption": _to_float(body["ActualConsumption"]),
            "diff_consumption": _to_float(body["DiffConsumption"]),
            "percent_consumption": (
                round(percent_consumption * 100, 1)
                if percent_consumption is not None
                else None
            ),
            "unit": body["Unit"],
        }

    async def get_meter_readings(self) -> list[dict]:
        menu_map = await self.get_menu_map()
        menu_id = menu_map["MeterReadings"]
        body = await self.request(
            "GET",
            "Flat/MeterReadings",
            params={"menuId": menu_id, "disassembled": 1},
        )
        return [
            {
                "meter_id": entry["MeterId"],
                "meter_number": entry["MeterNumber"],
                "meter_type": entry["MeterType"],
                "room": entry["ClimbingIron"],
            }
            for entry in body["MeterReadings"]
        ]

    async def get_account(self) -> dict:
        menu_map = await self.get_menu_map()
        menu_id = menu_map["account"]
        body = await self.request(
            "GET", "Flat/Account", params={"menuId": menu_id}
        )
        last_payment = next(
            (e for e in body["Account"] if e["TypeOfMovement"] == "P"), None
        )
        return {
            "due_balance": _to_float(body["DueBalance"]),
            "due_date": body["DueDate"],
            "last_payment_amount": _to_float(last_payment["Amount"]) if last_payment else None,
            "last_payment_date": last_payment["CreditDate"] if last_payment else None,
        }

    async def get_repair_fund(self) -> dict:
        """The repair fund balance accumulates since the fund started, not
        just within the current calendar year, but the ledger API only
        returns one year at a time. Past years are closed and cached
        forever once fetched; the current year is still in progress and is
        always re-fetched. Edge case: if the calendar year rolls over while
        this client stays alive without a restart, the year that used to be
        "current" won't get folded into the cache until the next restart —
        acceptable since HA restarts far more often than once a year."""
        menu_map = await self.get_menu_map()
        menu_id = menu_map["RepairFund"]
        current_year = datetime.now(timezone.utc).year

        if self._repair_fund_history is None:
            self._repair_fund_history = await self._fetch_repair_fund_history(
                menu_id, current_year
            )

        current_year_entries = await self._fetch_repair_fund_year(menu_id, current_year)
        all_entries = [
            entry
            for year_entries in self._repair_fund_history.values()
            for entry in year_entries
        ] + current_year_entries

        balance = sum(entry["amount"] or 0 for entry in all_entries)
        recent_entries = sorted(all_entries, key=lambda e: e["date"], reverse=True)[:5]
        since_year = min(self._repair_fund_history, default=current_year)

        return {
            "balance": balance,
            "year": current_year,
            "since_year": since_year,
            "recent_entries": recent_entries,
        }

    async def _fetch_repair_fund_year(self, menu_id: int, year: int) -> list[dict]:
        body = await self.request(
            "GET", "Object/RepairFund", params={"menuId": menu_id, "year": year}
        )
        return [
            {
                "amount": _to_float(e["Amount"]),
                "date": e["Date"],
                "description": e["Description"],
            }
            for e in body["RepairFund"]
        ]

    async def _fetch_repair_fund_history(
        self, menu_id: int, current_year: int
    ) -> dict[int, list[dict]]:
        history: dict[int, list[dict]] = {}
        for offset in range(1, REPAIR_FUND_LOOKBACK_YEARS_CAP + 1):
            year = current_year - offset
            entries = await self._fetch_repair_fund_year(menu_id, year)
            if not entries or not _entries_belong_to_year(entries, year):
                # Confirmed live: for years far enough back, the real API
                # stops honoring `year` and just echoes the current year's
                # ledger instead of returning empty. Treat that the same as
                # "no more history" — stop here and discard this response,
                # since counting it would double-count the same entries.
                break
            history[year] = entries
        else:
            _LOGGER.warning(
                "Repair fund history lookup hit the %s-year safety cap "
                "without finding the start of the fund's history; "
                "balance may be understated",
                REPAIR_FUND_LOOKBACK_YEARS_CAP,
            )
        return history

    async def close(self) -> None:
        if self._owns_session:
            await self._session.close()

    async def _raw_request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {self._id_token}"
        async with self._session.request(
            method, f"{BASE_URL}{path}", headers=headers, **kwargs
        ) as resp:
            return resp.status, await resp.json(content_type=None)

    async def request(self, method: str, path: str, **kwargs):
        if datetime.now(timezone.utc) >= self._refresh_after:
            await self._refresh()
        status, body = await self._raw_request(method, path, **kwargs)
        if status == 401:
            await self._refresh()
            status, body = await self._raw_request(method, path, **kwargs)
            if status == 401:
                raise PoschodochAuthError("Refresh token rejected")
        return body

    async def _refresh(self) -> None:
        async with self._session.post(
            f"{BASE_URL}Auth/refresh",
            data=json.dumps(
                {"AuthToken": self._id_token, "RefreshToken": self._id_refresh_token}
            ),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._id_token}",
            },
        ) as resp:
            if resp.status != 200:
                error_body = await resp.text()
                _LOGGER.debug(
                    "Auth/refresh rejected with status %s: %s",
                    resp.status,
                    error_body,
                )
                raise PoschodochAuthError(
                    f"Refresh token rejected (status {resp.status})"
                )
            body = await resp.json(content_type=None)

        # This intermediate token authenticates but is not yet bound to a
        # unit/portal — Dashboard/Flat/Object endpoints will 401 until
        # Auth/changeunit is called, exactly as the real client does after
        # every login. Set it now so the two calls below can use it.
        self._id_token = body["auth_token"]
        self._id_refresh_token = body["refresh_token"]

        await self._activate_unit()

    async def activate(self) -> None:
        """Bind the current, freshly-provided token to a unit/portal.

        Use this once, right after setup, for a token pair that has never
        been used with this API before (e.g. one copied straight out of a
        browser right after a Google login). Auth/refresh rejects a
        refresh_token that has never been through Auth/changeunit, so the
        very first activation must skip Auth/refresh and call
        Auth/UnitList + Auth/changeunit directly with the as-given token.
        """
        await self._activate_unit()

    async def _activate_unit(self) -> None:
        if self._portal_id is None:
            status, units = await self._raw_request("GET", "Auth/UnitList/")
            if status != 200 or not units:
                raise PoschodochAuthError(
                    f"Could not list units (status {status})"
                )
            self._portal_id = units[0]["PortalId"]

        status, body = await self._raw_request(
            "POST", "Auth/changeunit", params={"portalId": self._portal_id}
        )
        if status != 200:
            raise PoschodochAuthError(f"Could not activate unit (status {status})")

        self._id_token = body["auth_token"]
        self._id_refresh_token = body["refresh_token"]
        expires_in = timedelta(seconds=body.get("expires_in", TOKEN_LIFETIME.total_seconds()))
        now = datetime.now(timezone.utc)
        self._token_expires_at = now + expires_in
        self._refresh_after = now + max(expires_in - REFRESH_MARGIN, timedelta(0))

        if self._on_tokens_updated is not None:
            await self._on_tokens_updated(self._id_token, self._id_refresh_token)
