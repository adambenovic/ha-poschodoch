from unittest.mock import AsyncMock, patch

import pytest
from aioresponses import aioresponses
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.poschodoch.const import DOMAIN


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
            payload={"RepairFund": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2025",
            payload={"RepairFund": []},
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
            payload={"RepairFund": []},
        )
        mocked.get(
            "https://api.poschodoch.sk/api/Object/RepairFund?menuId=20&year=2025",
            payload={"RepairFund": []},
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
