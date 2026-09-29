from unittest.mock import MagicMock

import pytest

from custom_components.poschodoch.sensor import (
    AccountBalanceSensor,
    ConsumptionStatusSensor,
    DailyWaterSensor,
    HeatingRoomDailySensor,
    RepairFundBalanceSensor,
    async_setup_entry,
)


def make_coordinator():
    coordinator = MagicMock()
    coordinator.data = {
        "daily_consumption": {
            "S": [{"date": "2026-09-23", "consumption": 109.0}, {"date": "2026-09-24", "consumption": 290.0}],
            "T": [{"date": "2026-09-23", "consumption": 16.0}, {"date": "2026-09-24", "consumption": 54.0}],
        },
        "heating_daily_consumption": {
            "Kuchyňa": [{"date": "2026-09-24", "consumption": 1.5}],
            "Spálňa": [{"date": "2026-09-24", "consumption": 0.0}],
        },
        "consumption_status": {
            "S": {"actual_consumption": 38.28, "diff_consumption": 8.65, "percent_consumption": 29.0, "unit": "m3"},
            "T": {"actual_consumption": 10.0, "diff_consumption": 1.0, "percent_consumption": 5.0, "unit": "m3"},
            "U": {"actual_consumption": 644.0, "diff_consumption": -161.0, "percent_consumption": -20.0, "unit": "d./kWh"},
        },
        "account": {
            "due_balance": 195.60,
            "due_date": "2026-09-30",
            "last_payment_amount": 184.24,
            "last_payment_date": "2026-09-02",
        },
        "repair_fund": {"balance": 109.28, "year": 2026, "recent_entries": []},
    }
    return coordinator


def test_consumption_status_sensor_reports_percent_as_state():
    coordinator = make_coordinator()
    sensor = ConsumptionStatusSensor(coordinator, "S", "Cold water status")
    assert sensor.native_value == 29.0
    assert sensor.extra_state_attributes["actual_consumption"] == 38.28


def test_daily_water_sensor_reports_latest_day():
    coordinator = make_coordinator()
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.native_value == 290.0
    assert sensor.extra_state_attributes["date"] == "2026-09-24"


def test_heating_room_sensor_reports_latest_day():
    coordinator = make_coordinator()
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor.native_value == 1.5
    assert sensor.extra_state_attributes["date"] == "2026-09-24"


def test_account_balance_sensor_reports_due_balance():
    coordinator = make_coordinator()
    sensor = AccountBalanceSensor(coordinator)
    assert sensor.native_value == 195.60
    assert sensor.extra_state_attributes["last_payment_amount"] == 184.24


def test_repair_fund_sensor_reports_balance():
    coordinator = make_coordinator()
    sensor = RepairFundBalanceSensor(coordinator)
    assert sensor.native_value == 109.28
    assert sensor.extra_state_attributes["year"] == 2026


@pytest.mark.asyncio
async def test_async_setup_entry_creates_one_entity_per_room_plus_fixed_sensors():
    from custom_components.poschodoch.const import DOMAIN

    coordinator = make_coordinator()
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry-id": coordinator}}
    entry = MagicMock()
    entry.entry_id = "entry-id"

    added = []

    def async_add_entities(entities):
        added.extend(entities)

    await async_setup_entry(hass, entry, async_add_entities)

    room_sensors = [e for e in added if isinstance(e, HeatingRoomDailySensor)]
    assert len(room_sensors) == 2
    assert {s._room for s in room_sensors} == {"Kuchyňa", "Spálňa"}

    fixed_kinds = {type(e) for e in added} - {HeatingRoomDailySensor}
    assert ConsumptionStatusSensor in fixed_kinds
    assert DailyWaterSensor in fixed_kinds
    assert AccountBalanceSensor in fixed_kinds
    assert RepairFundBalanceSensor in fixed_kinds
