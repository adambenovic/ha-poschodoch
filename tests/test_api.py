from datetime import datetime, timedelta, timezone

import aiohttp
import pytest
import pytest_asyncio
from aioresponses import aioresponses
from yarl import URL

from custom_components.poschodoch.api import PoschodochApiClient, PoschodochAuthError


@pytest_asyncio.fixture
async def client_factory():
    created = []

    def _client_factory(**overrides):
        now = datetime.now(timezone.utc)
        defaults = dict(
            id_token="initial-id-token",
            id_refresh_token="initial-refresh-token",
            token_expires_at=now + timedelta(hours=2),
            refresh_after=now + timedelta(hours=1),
        )
        defaults.update(overrides)
        client = PoschodochApiClient(**defaults)
        created.append(client)
        return client

    yield _client_factory

    for client in created:
        await client.close()


@pytest.mark.asyncio
async def test_request_sends_current_id_token_as_auth_header(client_factory):
    client = client_factory(id_token="the-current-token")
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )
        await client.request("GET", "Dashboard/UnitInfo/")

    key = ("GET", URL("https://api.poschodoch.sk/api/Dashboard/UnitInfo/"))
    request = mocked.requests[key][0]
    assert request.kwargs["headers"]["X-Auth-Token"] == "the-current-token"


@pytest.mark.asyncio
async def test_request_refreshes_and_retries_once_on_401(client_factory):
    client = client_factory(id_token="stale-token", id_refresh_token="my-refresh-token")
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            status=401,
        )
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/Refresh",
            payload={"IdToken": "fresh-token", "IdRefreshToken": "fresh-refresh-token"},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )

        result = await client.request("GET", "Dashboard/UnitInfo/")

    assert result == {"UnitId": 1}

    refresh_key = ("POST", URL("https://api.poschodoch.sk/api/Auth/Refresh"))
    refresh_call = mocked.requests[refresh_key][0]
    assert refresh_call.kwargs["data"] == '"my-refresh-token"'

    retry_key = ("GET", URL("https://api.poschodoch.sk/api/Dashboard/UnitInfo/"))
    second_call = mocked.requests[retry_key][1]
    assert second_call.kwargs["headers"]["X-Auth-Token"] == "fresh-token"


@pytest.mark.asyncio
async def test_rejected_refresh_token_raises_auth_error(client_factory):
    client = client_factory(id_refresh_token="dead-refresh-token")
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            status=401,
        )
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/Refresh",
            status=401,
            payload={"error": "invalid refresh token"},
        )

        with pytest.raises(PoschodochAuthError):
            await client.request("GET", "Dashboard/UnitInfo/")


@pytest.mark.asyncio
async def test_proactively_refreshes_before_expiry_deadline(freezer, client_factory):
    now = datetime.now(timezone.utc)
    client = client_factory(
        id_token="soon-to-expire-token",
        id_refresh_token="my-refresh-token",
        refresh_after=now - timedelta(minutes=1),
        token_expires_at=now + timedelta(minutes=59),
    )
    with aioresponses() as mocked:
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/Refresh",
            payload={"IdToken": "fresh-token", "IdRefreshToken": "fresh-refresh-token"},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )

        await client.request("GET", "Dashboard/UnitInfo/")

    refresh_key = ("POST", URL("https://api.poschodoch.sk/api/Auth/Refresh"))
    assert refresh_key in mocked.requests

    call_key = ("GET", URL("https://api.poschodoch.sk/api/Dashboard/UnitInfo/"))
    call = mocked.requests[call_key][0]
    assert call.kwargs["headers"]["X-Auth-Token"] == "fresh-token"


@pytest.mark.asyncio
async def test_does_not_refresh_again_immediately_after_refreshing(freezer, client_factory):
    now = datetime.now(timezone.utc)
    client = client_factory(
        id_token="soon-to-expire-token",
        id_refresh_token="my-refresh-token",
        refresh_after=now - timedelta(minutes=1),
        token_expires_at=now + timedelta(minutes=59),
    )
    with aioresponses() as mocked:
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/Refresh",
            payload={"IdToken": "fresh-token", "IdRefreshToken": "fresh-refresh-token"},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )
        await client.request("GET", "Dashboard/UnitInfo/")

        # second call, same frozen time: must not refresh again
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )
        await client.request("GET", "Dashboard/UnitInfo/")

    refresh_key = ("POST", URL("https://api.poschodoch.sk/api/Auth/Refresh"))
    assert len(mocked.requests[refresh_key]) == 1


@pytest.mark.asyncio
async def test_close_closes_the_underlying_session(client_factory):
    client = client_factory()
    await client.close()
    assert client._session.closed


@pytest.mark.asyncio
async def test_close_does_not_close_an_externally_provided_session(client_factory):
    session = aiohttp.ClientSession()
    client = client_factory(session=session)
    await client.close()
    assert not session.closed
    await session.close()


@pytest.mark.asyncio
async def test_get_menu_map_resolves_menu_code_to_menu_id(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
            ],
        )
        menu_map = await client.get_menu_map()

    assert menu_map == {"account": 1, "DailyConsumption": 41}


@pytest.mark.asyncio
async def test_get_menu_map_is_cached_and_does_not_refetch(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 1, "MenuCode": "account", "MenuName": "..."}],
        )
        await client.get_menu_map()
        # no second mock registered: a second fetch would raise ClientConnectionError
        menu_map = await client.get_menu_map()

    assert menu_map == {"account": 1}


@pytest.mark.asyncio
async def test_get_daily_consumption_partitions_by_code_field(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={
                "Consumption": [
                    {"Date": "2026-09-24", "Code": "S", "Type": "SV", "Consumption": "290.000"},
                    {"Date": "2026-09-24", "Code": "T", "Type": "SV-Teplá voda", "Consumption": "54.000"},
                ]
            },
        )

        result = await client.get_daily_consumption()

    assert result["S"][0]["consumption"] == 290.0
    assert result["S"][0]["date"] == "2026-09-24"
    assert result["T"][0]["consumption"] == 54.0


@pytest.mark.asyncio
async def test_get_consumption_status_parses_percentages_and_amounts(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type=S",
            payload={
                "Type": "S",
                "Unit": "m3",
                "ActualConsumption": "38.28",
                "DiffConsumption": "8.65",
                "PercConsumption": "0.29",
            },
        )

        result = await client.get_consumption_status("S")

    assert result["actual_consumption"] == 38.28
    assert result["diff_consumption"] == 8.65
    assert result["percent_consumption"] == 29.0
    assert result["unit"] == "m3"


@pytest.mark.asyncio
async def test_get_meter_readings_returns_meter_list(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={
                "MeterReadings": [
                    {
                        "MeterId": 512927,
                        "MeterNumber": "138034137",
                        "MeterType": "UK",
                        "ClimbingIron": "Detská izba",
                    },
                    {
                        "MeterId": 557015,
                        "MeterNumber": "148004366",
                        "MeterType": "SV",
                        "ClimbingIron": "6",
                    },
                ]
            },
        )

        result = await client.get_meter_readings()

    assert result[0]["meter_id"] == 512927
    assert result[0]["meter_type"] == "UK"
    assert result[0]["room"] == "Detská izba"


@pytest.mark.asyncio
async def test_get_account_returns_balance_and_last_payment(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 1, "MenuCode": "account", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={
                "DueBalance": "195.60",
                "DueDate": "2026-09-30",
                "Account": [
                    {
                        "TypeOfMovement": "P",
                        "Amount": "184.24",
                        "CreditDate": "2026-09-02",
                    },
                    {
                        "TypeOfMovement": "N",
                        "Amount": "-184.24",
                        "CreditDate": "2026-09-30",
                    },
                ],
            },
        )

        result = await client.get_account()

    assert result["due_balance"] == 195.60
    assert result["due_date"] == "2026-09-30"
    assert result["last_payment_amount"] == 184.24
    assert result["last_payment_date"] == "2026-09-02"


@pytest.mark.asyncio
async def test_get_repair_fund_sums_current_year_ledger(freezer, client_factory):
    freezer.move_to("2026-09-29")
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={
                "RepairFund": [
                    {"Amount": "1053.98", "Date": "2026-08-31", "Description": "TVORBA FO"},
                    {"Amount": "-226.42", "Date": "2026-09-21", "Description": "Splatka"},
                    {"Amount": "-718.28", "Date": "2026-09-21", "Description": "Splatka uveru"},
                ]
            },
        )

        result = await client.get_repair_fund()

    assert result["balance"] == pytest.approx(109.28)
    assert result["year"] == 2026
    assert len(result["recent_entries"]) == 3


@pytest.mark.asyncio
async def test_get_heating_daily_consumption_groups_by_room(client_factory):
    client = client_factory()
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[{"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."}],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={
                "Consumption": [
                    {"Date": "2026-09-01", "Code": "U", "Type": "Spálňa", "Consumption": "0.000"},
                    {"Date": "2026-09-01", "Code": "U", "Type": "Kuchyňa", "Consumption": "1.500"},
                    {"Date": "2026-09-02", "Code": "U", "Type": "Spálňa", "Consumption": "2.000"},
                ]
            },
        )

        result = await client.get_heating_daily_consumption()

    assert result["Spálňa"][0]["consumption"] == 0.0
    assert result["Spálňa"][1]["consumption"] == 2.0
    assert result["Kuchyňa"][0]["consumption"] == 1.5


@pytest.mark.asyncio
async def test_refresh_calls_on_tokens_updated_callback_with_new_tokens(client_factory):
    calls = []

    async def on_tokens_updated(id_token, id_refresh_token):
        calls.append((id_token, id_refresh_token))

    client = client_factory(
        id_token="stale-token",
        id_refresh_token="old-refresh-token",
        on_tokens_updated=on_tokens_updated,
    )
    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            status=401,
        )
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/Refresh",
            payload={"IdToken": "new-token", "IdRefreshToken": "rotated-refresh-token"},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/UnitInfo/",
            payload={"UnitId": 1},
        )

        await client.request("GET", "Dashboard/UnitInfo/")

    assert calls == [("new-token", "rotated-refresh-token")]
