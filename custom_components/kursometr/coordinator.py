"""Координатор обновления курсов (официальный XML-сервис ЦБ РФ)."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, timedelta
import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DOMAIN,
    METAL_CODES,
)
from .xml_api import CbrXmlApi, DailyRates, XmlApiError, previous_weekday


def _previous_weekday_iso(day: str | None) -> str | None:
    """Дата предыдущего рабочего дня в формате YYYY-MM-DD (или None)."""
    if not day:
        return None
    try:
        parsed = date.fromisoformat(day[:10])
    except ValueError:
        return None
    return previous_weekday(parsed).isoformat()

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
        # Локальный кеш «дата -> курс»: история накапливается с каждым
        # обновлением, поэтому вчерашний курс доступен даже при недоступности
        # сервиса ЦБ. Прогноз на завтра живёт до смены текущей даты.
        self._history: OrderedDict[str, float | None] = OrderedDict()
        self._forecast: RateData | None = None

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

        # История накапливается локально: при смене даты текущий курс
        # записывается в кеш, а прогноз «на завтра» теряет силу.
        self._remember(data)
        self._forecast = None
        return data

    # --- История и прогноз -------------------------------------------------

    async def async_get_yesterday(self) -> RateData | None:
        """Курс за предыдущий рабочий день — из кеша или одним запросом ЦБ."""
        today = self.data.date if self.data else None
        prev_day = _previous_weekday_iso(today)

        if prev_day is not None and prev_day in self._history:
            rate = self._history[prev_day]
            if rate is not None:
                base = self.data
                nominal = base.nominal if base else 1
                return RateData(
                    rate=rate,
                    name=base.name if base else self.currency,
                    nominal=nominal,
                    value=round(rate * nominal, 4),
                    date=prev_day,
                    unit=base.unit if base else "RUB",
                )

        try:
            rates = await self.api.async_get_yesterday_rates()
        except XmlApiError as err:
            _LOGGER.debug("Вчерашний курс %s недоступен: %s", self.currency, err)
            return None
        data = _extract(rates, self.currency, self.is_metal)
        if data is not None:
            self._remember(data)
        return data

    async def async_get_tomorrow(self) -> RateData | None:
        """Курс «на завтра»: ЦБ публикует его заранее только в рабочие дни.

        Успешный результат кешируется в ``_forecast``; если данных ещё нет,
        сенсор показывает временную заглушку до следующей попытки обновления.
        """
        if self._forecast is not None:
            return self._forecast
        try:
            rates = await self.api.async_get_tomorrow_rates()
        except XmlApiError as err:
            _LOGGER.debug("Завтрашний курс %s недоступен: %s", self.currency, err)
            return None
        if rates is None:
            return None
        data = _extract(rates, self.currency, self.is_metal)
        if data is not None:
            self._forecast = data
        return data

    def _remember(self, data: RateData) -> None:
        """Пишет курс в локальный кеш истории (не более ~40 дат)."""
        self._history[data.date] = data.rate
        while len(self._history) > 40:  # запас на длинные праздники
            self._history.popitem(last=False)
