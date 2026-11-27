"""Сенсоры курсов Курсометра.

На каждый выбранный инструмент создаются три сенсора: текущий курс,
курс за предыдущий рабочий день («вчера») и курс «на завтра». Последний
ЦБ публикует заранее только в рабочие дни — до появления данных сенсор
«на завтра» остаётся недоступным (``unavailable``), а статус с подсказкой
когда появятся значения показывает его атрибут ``status``.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_CURRENCY,
    ATTR_DATE,
    ATTR_NAME,
    ATTR_NOMINAL,
    ATTR_VALUE,
    DOMAIN,
)
from .coordinator import RateData

_LOGGER = logging.getLogger(__name__)

# Подсказка в атрибуте status, пока ЦБ не опубликовал значение
# (например, курс «на завтра» в выходные или до вечерней публикации).
STATUS_PENDING = "Данные будут опубликованы после 18:30 МСК"
STATUS_OK = "ok"

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
    history = KursometrHistorySensor(coordinator, entry)
    forecast = KursometrForecastSensor(coordinator, entry)
    async_add_entities(
        [KursometrSensor(coordinator, entry), history, forecast]
    )

    # Вчерашний и завтрашний курсы приходят отдельными запросами к ЦБ,
    # поэтому обновляем их независимо от основного координатора:
    # сразу после добавления сенсоров и далее каждые сутки (ЦБ публикует
    # завтрашние курсы вечером предыдущего рабочего дня, около 18:30 МСК).
    async def _refresh_extras(_now=None):
        await asyncio.gather(
            history.async_refresh_extra(), forecast.async_refresh_extra()
        )

    entry.async_on_unload(
        async_track_time_interval(hass, _refresh_extras, timedelta(days=1))
    )
    entry.async_create_background_task(
        hass, _refresh_extras(), f"{DOMAIN}_extras_{entry.entry_id}"
    )


class KursometrSensor(CoordinatorEntity, SensorEntity):
    """Текущий курс выбранной валюты или металла."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT

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


class _KursometrDaySensor(CoordinatorEntity, SensorEntity):
    """Общая часть сенсоров «вчера» и «на завтра».

    Эти значения приходят не из координатора (он отдаёт только текущий
    курс), а из отдельных запросов к сервису ЦБ; ответ кешируется в
    ``_extra``. Пока данных нет, сенсор недоступен (``unavailable``) —
    текстовые заглушки в числовом сенсоре HA запрещает.
    """

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:clock-outline"

    def __init__(self, coordinator, entry, suffix: str):
        super().__init__(coordinator)
        self._suffix = suffix
        self._extra: RateData | None = None
        self._attr_unique_id = (
            f"{entry.entry_id}_{coordinator.currency}_{suffix}"
        )
        self._attr_name = f"Курс {coordinator.currency} ({suffix})"

    async def async_refresh_extra(self) -> None:
        raise NotImplementedError

    @callback
    def _set_extra(self, data: RateData | None) -> None:
        self._extra = data
        self.async_write_ha_state()

    @property
    def available(self):
        # Доступность следует за наличием числовых данных.
        return self.coordinator.last_update_success and self._extra is not None

    @property
    def native_unit_of_measurement(self):
        return self._extra.unit if self._extra else None

    @property
    def native_value(self):
        return self._extra.rate if self._extra else None

    @property
    def extra_state_attributes(self):
        if self._extra is None:
            return {
                ATTR_CURRENCY: self.coordinator.currency,
                "status": STATUS_PENDING,
            }
        data = self._extra
        return {
            ATTR_CURRENCY: self.coordinator.currency,
            ATTR_NAME: data.name,
            ATTR_NOMINAL: data.nominal,
            ATTR_VALUE: data.value,
            ATTR_DATE: data.date,
            "status": STATUS_OK,
        }


class KursometrHistorySensor(_KursometrDaySensor):
    """Курс за предыдущий рабочий день."""

    _attr_icon = "mdi:history"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "вчера")

    async def async_refresh_extra(self) -> None:
        self._set_extra(await self.coordinator.async_get_yesterday())


class KursometrForecastSensor(_KursometrDaySensor):
    """Курс «на завтра» — публикуется ЦБ заранее только в рабочие дни."""

    _attr_icon = "mdi:weather-sunset-up"

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry, "завтра")

    async def async_refresh_extra(self) -> None:
        self._set_extra(await self.coordinator.async_get_tomorrow())
