"""Координатор обновления курсов (официальный XML-сервис ЦБ РФ)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    METAL_CODES,
)
from .xml_api import CbrXmlApi, DailyRates, XmlApiError

_LOGGER = logging.getLogger(__name__)


@dataclass
class RateData:
    rate: float
    name: str
    nominal: int
    value: float
    date: str
    unit: str
    available: bool = True


def _extract(rates: DailyRates, currency: str, is_metal: bool) -> RateData | None:
    """Достаёт данные выбранного инструмента из ответа за день.

    ``None`` означает «данных на эту дату нет» (например, курс на завтра
    ещё не опубликован) — сенсор покажет временную заглушку вместо ошибки.
    """
    if is_metal:
        metal = rates.metals.get(currency)
        if metal is None:
            return None
        return RateData(
            rate=metal.price,
            name=metal.name,
            nominal=1,
            value=metal.price,
            date=rates.date,
            unit=metal.unit,
        )

    item = rates.currencies.get(currency)
    if item is None:
        return None
    name, nominal, rate = item
    return RateData(
        rate=rate,
        name=name,
        nominal=nominal,
        value=round(rate * nominal, 4),
        date=rates.date,
        unit="RUB",
    )


class KursometrCoordinator(DataUpdateCoordinator):
    def __init__(
        self,
        hass: HomeAssistant,
        api: CbrXmlApi,
        currency: str,
        scan_interval: int,
    ):
        super().__init__(
            hass,
            logger=_LOGGER,
            name=f"{DOMAIN} {currency}",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        self.currency = currency
        self.is_metal = currency in METAL_CODES

    async def _async_update_data(self) -> RateData:
        try:
            rates = await self.api.async_get_daily_rates()
        except XmlApiError as err:
            raise UpdateFailed(str(err)) from err

        data = _extract(rates, self.currency, self.is_metal)
        if data is None:
            if self.is_metal:
                raise UpdateFailed(f"ЦБ не публикует цену {self.currency}")
            raise UpdateFailed(f"ЦБ больше не публикует {self.currency}")
        return data

    async def async_get_yesterday(self) -> RateData | None:
        """Курс за предыдущий рабочий день (или None при недоступности)."""
        try:
            rates = await self.api.async_get_yesterday_rates()
        except XmlApiError as err:
            _LOGGER.debug("Вчерашний курс %s недоступен: %s", self.currency, err)
            return None
        data = _extract(rates, self.currency, self.is_metal)
        if data is not None:
            data.available = False
        return data

    async def async_get_tomorrow(self) -> RateData | None:
        """Курс «на завтра»: ЦБ публикует его заранее только в рабочие дни."""
        try:
            rates = await self.api.async_get_tomorrow_rates()
        except XmlApiError as err:
            _LOGGER.debug("Завтрашний курс %s недоступен: %s", self.currency, err)
            return None
        if rates is None:
            return None
        data = _extract(rates, self.currency, self.is_metal)
        if data is not None:
            data.available = False
        return data
