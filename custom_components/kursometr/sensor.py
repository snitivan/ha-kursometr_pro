"""Сенсор курса Курсометра."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CURRENCY,
    ATTR_DATE,
    ATTR_NAME,
    ATTR_NOMINAL,
    ATTR_VALUE,
    DOMAIN,
)

ICONS = {
    "USD": "mdi:currency-usd",
    "EUR": "mdi:currency-eur",
    "GBP": "mdi:currency-gbp",
    "JPY": "mdi:currency-jpy",
    "CNY": "mdi:currency-cny",
    "RUB": "mdi:currency-rub",
    "KRW": "mdi:currency-krw",
    "TRY": "mdi:currency-try",
    "CHF": "mdi:currency-chf",
    "GOLD": "mdi:gold",
    "SILVER": "mdi:silver",
}


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KursometrSensor(coordinator, entry)])


class KursometrSensor(CoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.MONETARY

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{coordinator.currency}"
        self._attr_name = f"Курс {coordinator.currency}"
        self._attr_icon = ICONS.get(coordinator.currency, "mdi:cash")

    @property
    def native_unit_of_measurement(self):
        data = self.coordinator.data
        return data.unit if data else "RUB"

    @property
    def native_value(self):
        data = self.coordinator.data
        return data.rate if data else None

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        if not data:
            return None
        return {
            ATTR_CURRENCY: self.coordinator.currency,
            ATTR_NAME: data.name,
            ATTR_NOMINAL: data.nominal,
            ATTR_VALUE: data.value,
            ATTR_DATE: data.date,
        }
