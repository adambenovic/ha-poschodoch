from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poschodoch.api import PoschodochApiClient
from custom_components.poschodoch.const import DAILY_POLL_HOUR, DOMAIN


@pytest.mark.asyncio
async def test_setup_entry_creates_working_coordinator(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert result is True
    assert entry.state.value == "loaded"
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data["account"]["due_balance"] == 0.0

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_setup_entry_schedules_daily_refresh_at_fixed_local_time(hass):
    """Polling is anchored to a fixed local time (not a plain interval,
    which can't express "once a day at 6am") and the listener must be
    torn down on unload, not leaked."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        unsub = MagicMock()
        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ), patch(
            "custom_components.poschodoch.async_track_time_change",
            return_value=unsub,
        ) as mock_track:
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert result is True
    assert mock_track.call_args.kwargs["hour"] == DAILY_POLL_HOUR
    assert mock_track.call_args.kwargs["minute"] == 0
    assert mock_track.call_args.kwargs["second"] == 0
    assert unsub in entry._on_unload

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)

    unsub.assert_called_once()


@pytest.mark.asyncio
async def test_setup_entry_passes_saved_password_login_fields_to_client(hass):
    """Entries set up via email+password must have those fields (plus the
    device-trust cookie) reach the client, since that's what gates the
    self-heal fallback in _refresh(). Existing token-paste entries simply
    don't have these keys at all — entry.data.get(...) returns None for
    them, which is exactly what keeps today's behavior unchanged."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
            "username": "user@example.com",
            "password": "hunter2",
            "device_cookie": "saved-cookie",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ), patch(
            "custom_components.poschodoch.PoschodochApiClient",
            wraps=PoschodochApiClient,
        ) as mock_client_cls:
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert result is True
    assert mock_client_cls.call_args.kwargs["username"] == "user@example.com"
    assert mock_client_cls.call_args.kwargs["password"] == "hunter2"
    assert mock_client_cls.call_args.kwargs["device_cookie"] == "saved-cookie"

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_full_setup_creates_real_sensor_entities(hass):
    """End-to-end: no mocking of platform forwarding, exercises the real
    __init__.py -> sensor.py wiring exactly as Home Assistant would."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={
                "Consumption": [
                    {"Date": "2026-09-24", "Code": "S", "Type": "SV", "Consumption": "290.000"},
                    {"Date": "2026-09-24", "Code": "T", "Type": "TV", "Consumption": "54.000"},
                ]
            },
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={
                "Consumption": [
                    {"Date": "2026-09-24", "Code": "U", "Type": "Kuchyňa", "Consumption": "1.500"},
                ]
            },
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "195.60", "DueDate": "2026-09-30", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        result = await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert result is True
    assert entry.state.value == "loaded"

    balance_state = hass.states.get("sensor.account_balance")
    assert balance_state is not None
    assert balance_state.state == "195.6"

    heating_state = hass.states.get("sensor.heating_kuchyna")
    assert heating_state is not None
    assert heating_state.state == "1.5"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_token_rotation_persists_new_expiry_timestamps(hass):
    """A rotation mid-session must persist the *new* token_expires_at/
    refresh_after too, not just id_token/id_refresh_token — otherwise a
    restart right after a rotation starts the client back up with stale
    expiry timestamps from whenever the entry was first created."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "stale-token",
            "id_refresh_token": "stale-refresh-token",
            # Already due for a proactive refresh on the very first request.
            "token_expires_at": "2000-01-01T00:00:00+00:00",
            "refresh_after": "2000-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/refresh",
            payload={
                "auth_token": "intermediate-token",
                "refresh_token": "intermediate-refresh-token",
                "expires_in": 7200,
            },
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Auth/UnitList/",
            payload=[{"PortalId": 78159}],
        )
        mocked.post(
            "https://api.poschodoch.sk/api/Auth/changeunit?portalId=78159",
            payload={
                "auth_token": "activated-token",
                "refresh_token": "activated-refresh-token",
                "expires_in": 7200,
            },
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ), patch(
            "custom_components.poschodoch.statistics.async_backfill",
            new=AsyncMock(),
        ):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert entry.data["id_token"] == "activated-token"
    assert entry.data["token_expires_at"] != "2000-01-01T00:00:00+00:00"
    assert entry.data["refresh_after"] != "2000-01-01T00:00:00+00:00"

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_setup_entry_triggers_statistics_backfill(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ), patch(
            "custom_components.poschodoch.statistics.async_backfill",
            new=AsyncMock(),
        ) as mock_backfill:
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    mock_backfill.assert_called_once()

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.asyncio
async def test_setup_entry_survives_statistics_backfill_failure(hass):
    """A backfill failure (e.g. recorder not ready yet) must never prevent
    the integration from loading."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "id_token": "some-token",
            "id_refresh_token": "some-refresh-token",
            "token_expires_at": "2099-01-01T00:00:00+00:00",
            "refresh_after": "2099-01-01T00:00:00+00:00",
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(
            "https://api.poschodoch.sk/api/Dashboard/Menu",
            payload=[
                {"MenuId": 41, "MenuCode": "DailyConsumption", "MenuName": "..."},
                {"MenuId": 47, "MenuCode": "ConsumptionStatus", "MenuName": "..."},
                {"MenuId": 14, "MenuCode": "MeterReadings", "MenuName": "..."},
                {"MenuId": 1, "MenuCode": "account", "MenuName": "..."},
                {"MenuId": 20, "MenuCode": "RepairFund", "MenuName": "..."},
            ],
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=S",
            payload={"Consumption": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/DailyConsumption?menuId=41&type=U",
            payload={"Consumption": []},
        )
        for type_code in ("S", "T", "U"):
            mocked.get(
                f"https://api.poschodoch.sk/api/Flat/ConsumptionStatus?menuId=47&type={type_code}",
                payload={
                    "ActualConsumption": "0",
                    "DiffConsumption": "0",
                    "PercConsumption": "0",
                    "Unit": "m3",
                },
            )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/MeterReadings?menuId=14&disassembled=1",
            payload={"MeterReadings": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Flat/Account?menuId=1",
            payload={"DueBalance": "0", "DueDate": "2026-01-01", "Account": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2026",
            payload={"YearFrom": 2026, "YearTo": 2026, "FinalBalance": "0", "RepairFund": []},
        )

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_forward_entry_setups",
            new=AsyncMock(return_value=True),
        ), patch(
            "custom_components.poschodoch.statistics.async_backfill",
            new=AsyncMock(side_effect=RuntimeError("recorder not ready")),
        ):
            result = await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()

    assert result is True
    assert entry.state.value == "loaded"

    with patch(
        "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
        new=AsyncMock(return_value=True),
    ):
        assert await hass.config_entries.async_unload(entry.entry_id)
