"""Sensor platform for poschodoch.sk."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN


def _latest_reading(readings: list[dict]) -> dict:
    """The most recent reading with an actual value. Today's entry can
    still be null (meter hasn't reported yet) even though older days are
    already available."""
    for reading in reversed(readings):
        if reading["consumption"] is not None:
            return reading
    return readings[-1]


class _PoschodochSensorBase(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator, name: str, unique_id: str) -> None:
        super().__init__(coordinator)
        self._attr_name = name
        self._attr_unique_id = unique_id


class ConsumptionStatusSensor(_PoschodochSensorBase):
    """'How am I doing vs last year' indicator for water/heating."""

    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator, type_code: str, name: str) -> None:
        super().__init__(coordinator, name, f"poschodoch_status_{type_code}")
        self._type_code = type_code

    @property
    def native_value(self):
        return self.coordinator.data["consumption_status"][self._type_code][
            "percent_consumption"
        ]

    @property
    def extra_state_attributes(self):
        status = self.coordinator.data["consumption_status"][self._type_code]
        return {
            "actual_consumption": status["actual_consumption"],
            "diff_consumption": status["diff_consumption"],
            "unit": status["unit"],
        }


class DailyWaterSensor(_PoschodochSensorBase):
    """Latest daily water reading (cold or hot)."""

    _attr_native_unit_of_measurement = "L"

    def __init__(self, coordinator, code: str, name: str) -> None:
        super().__init__(coordinator, name, f"poschodoch_daily_water_{code}")
        self._code = code

    @property
    def _latest(self):
        return _latest_reading(self.coordinator.data["daily_consumption"][self._code])

    @property
    def native_value(self):
        return self._latest["consumption"]

    @property
    def extra_state_attributes(self):
        return {
            "date": self._latest["date"],
            "average_last_30_days": self.coordinator.data["rolling_averages"].get(
                self._code
            ),
        }


class HeatingRoomDailySensor(_PoschodochSensorBase):
    """Latest daily heat-cost-allocator reading for one room."""

    def __init__(self, coordinator, room: str) -> None:
        super().__init__(
            coordinator, f"Heating - {room}", f"poschodoch_heating_{room}"
        )
        self._room = room

    @property
    def _latest(self):
        return _latest_reading(
            self.coordinator.data["heating_daily_consumption"][self._room]
        )

    @property
    def native_value(self):
        return self._latest["consumption"]

    @property
    def extra_state_attributes(self):
        return {
            "date": self._latest["date"],
            "average_last_30_days": self.coordinator.data["rolling_averages"].get(
                self._room
            ),
        }


class AccountBalanceSensor(_PoschodochSensorBase):
    """Current account balance (due or credit)."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = "monetary"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "Account balance", "poschodoch_account_balance")

    @property
    def native_value(self):
        return self.coordinator.data["account"]["due_balance"]

    @property
    def extra_state_attributes(self):
        account = self.coordinator.data["account"]
        return {
            "due_date": account["due_date"],
            "last_payment_amount": account["last_payment_amount"],
            "last_payment_date": account["last_payment_date"],
        }


class RepairFundBalanceSensor(_PoschodochSensorBase):
    """Repair fund balance for the current year."""

    _attr_native_unit_of_measurement = "EUR"
    _attr_device_class = "monetary"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "Repair fund balance", "poschodoch_repair_fund")

    @property
    def native_value(self):
        return self.coordinator.data["repair_fund"]["balance"]

    @property
    def extra_state_attributes(self):
        fund = self.coordinator.data["repair_fund"]
        return {
            "year": fund["year"],
            "since_year": fund.get("since_year"),
            "recent_entries": fund["recent_entries"],
        }


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        ConsumptionStatusSensor(coordinator, "S", "Cold water status"),
        ConsumptionStatusSensor(coordinator, "T", "Hot water status"),
        ConsumptionStatusSensor(coordinator, "U", "Heating status"),
        DailyWaterSensor(coordinator, "S", "Cold water daily"),
        DailyWaterSensor(coordinator, "T", "Hot water daily"),
        AccountBalanceSensor(coordinator),
        RepairFundBalanceSensor(coordinator),
    ]
    for room in coordinator.data["heating_daily_consumption"]:
        entities.append(HeatingRoomDailySensor(coordinator, room))

    async_add_entities(entities)
