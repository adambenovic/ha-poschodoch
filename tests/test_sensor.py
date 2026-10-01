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
        "rolling_averages": {"S": 199.5, "T": 35.2, "Kuchyňa": 1.2, "Spálňa": 0.1},
    }
    return coordinator


def test_sensor_stays_available_when_data_present_even_if_last_poll_failed():
    """A failed poll (auth hiccup, transient API error, etc.) must not
    make entities go unavailable — HA's own DataUpdateCoordinator already
    keeps the last successful payload in .data through a failed update;
    only the default available property (last_update_success-based)
    ignores that and flips anyway."""
    coordinator = make_coordinator()
    coordinator.last_update_success = False
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.available is True


def test_sensor_unavailable_when_no_data_ever_fetched():
    coordinator = make_coordinator()
    coordinator.data = None
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.available is False


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


def test_daily_water_sensor_reports_rolling_average():
    coordinator = make_coordinator()
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.extra_state_attributes["average_last_30_days"] == 199.5


def test_daily_water_sensor_rolling_average_none_when_unavailable():
    coordinator = make_coordinator()
    coordinator.data["rolling_averages"] = {}
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.extra_state_attributes["average_last_30_days"] is None


def test_heating_room_sensor_unique_id_is_slugified():
    """statistics.py's statistic_id for the same room already uses
    slugify() (f"{DOMAIN}:heating_daily_{slugify(room)}") — unique_id must
    use the same identity scheme, or a room name with diacritics/spaces
    gets two different identities: one raw for the entity, one slugified
    for its long-term statistic."""
    coordinator = make_coordinator()
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor._attr_unique_id == "poschodoch_heating_kuchyna"


def test_heating_room_sensor_reports_latest_day():
    coordinator = make_coordinator()
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor.native_value == 1.5
    assert sensor.extra_state_attributes["date"] == "2026-09-24"


def test_heating_room_sensor_reports_rolling_average():
    coordinator = make_coordinator()
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor.extra_state_attributes["average_last_30_days"] == 1.2


def test_daily_water_sensor_skips_trailing_null_reading():
    """Today's reading can still be null (meter hasn't reported yet) even
    though yesterday's is already available — show that instead of
    'unknown'."""
    coordinator = make_coordinator()
    coordinator.data["daily_consumption"]["S"].append(
        {"date": "2026-09-25", "consumption": None}
    )
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.native_value == 290.0
    assert sensor.extra_state_attributes["date"] == "2026-09-24"


def test_heating_room_sensor_skips_trailing_null_reading():
    coordinator = make_coordinator()
    coordinator.data["heating_daily_consumption"]["Kuchyňa"].append(
        {"date": "2026-09-25", "consumption": None}
    )
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor.native_value == 1.5
    assert sensor.extra_state_attributes["date"] == "2026-09-24"


def test_daily_water_sensor_native_value_none_when_series_missing():
    """A flat with no hot-water metering never has "T" in the response at
    all (partitioned dict only gets keys for codes actually present) — the
    sensor must go 'unknown' instead of raising KeyError on every poll."""
    coordinator = make_coordinator()
    del coordinator.data["daily_consumption"]["T"]
    sensor = DailyWaterSensor(coordinator, "T", "Hot water daily")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["date"] is None


def test_daily_water_sensor_native_value_none_when_series_empty():
    """Early on the 1st of a month, before any reading has landed, the API
    can return an empty Consumption list entirely."""
    coordinator = make_coordinator()
    coordinator.data["daily_consumption"]["S"] = []
    sensor = DailyWaterSensor(coordinator, "S", "Cold water daily")
    assert sensor.native_value is None


def test_heating_room_sensor_native_value_none_when_room_missing():
    """A room can drop out of a later poll's response (allocator swap, no
    data yet for the new month) even though its entity persists across
    restarts via unique_id."""
    coordinator = make_coordinator()
    del coordinator.data["heating_daily_consumption"]["Kuchyňa"]
    sensor = HeatingRoomDailySensor(coordinator, "Kuchyňa")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["date"] is None


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
