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
    METAL_GOLD_CODE,
    METALS_BY_CODE,
)
from .xml_api import CbrXmlApi, XmlApiError

_LOGGER = logging.getLogger(__name__)


@dataclass
class RateData:
    rate: float
    name: str
    nominal: int
    value: float
    date: str
    unit: str


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

        if self.is_metal:
            metal = rates.metals.get(self.currency)
            if metal is None:
                raise UpdateFailed(f"ЦБ не публикует цену {self.currency}")
            return RateData(
                rate=metal.price,
                name=metal.name,
                nominal=1,
                value=metal.price,
                date=rates.date,
                unit=METALS_BY_CODE[
                    "1" if self.currency == METAL_GOLD_CODE else "2"
                ][1],
            )

        item = rates.currencies.get(self.currency)
        if item is None:
            raise UpdateFailed(f"ЦБ больше не публикует {self.currency}")
        name, nominal, rate = item
        return RateData(
            rate=rate,
            name=name,
            nominal=nominal,
            value=round(rate * nominal, 4),
            date=rates.date,
            unit="RUB",
        )
