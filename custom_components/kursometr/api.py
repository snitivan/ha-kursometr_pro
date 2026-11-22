"""Клиент дневного JSON-конца Банка России."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiohttp

from .const import CBR_API_URL


class CbrApiError(Exception):
    pass


class CbrConnectionError(CbrApiError):
    pass


class CbrInvalidResponseError(CbrApiError):
    pass


@dataclass
class CurrencyRate:
    code: str
    name: str
    nominal: int
    value: float
    rate: float


@dataclass
class CbrRates:
    date: str
    currencies: dict[str, CurrencyRate]


class CbrApi:
    def __init__(self, session: aiohttp.ClientSession, url: str = CBR_API_URL):
        self._session = session
        self._url = url

    async def async_get_payload(self) -> dict:
        try:
            async with self._session.get(
                self._url,
                timeout=aiohttp.ClientTimeout(total=15),
                headers={"Accept": "application/json"},
            ) as response:
                response.raise_for_status()
                return await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CbrConnectionError(str(err)) from err
        except (ValueError, TypeError) as err:
            raise CbrInvalidResponseError("ЦБ прислал не JSON") from err

    async def async_get_rates(self) -> CbrRates:
        payload = await self.async_get_payload()

        raw = payload.get("Valute")
        if not isinstance(raw, dict) or not raw:
            raise CbrInvalidResponseError("В ответе нет Valute")

        currencies = {}
        for code, item in raw.items():
            try:
                value = float(item["Value"])
                nominal = float(item["Nominal"])
            except (KeyError, TypeError, ValueError):
                continue
            if nominal <= 0:
                continue
            currencies[code] = CurrencyRate(
                code=item.get("CharCode", code),
                name=item.get("Name", code),
                nominal=int(nominal),
                value=value,
                rate=value / nominal,
            )

        if not currencies:
            raise CbrInvalidResponseError("Все валюты отбракованы")

        return CbrRates(date=payload.get("Date", ""), currencies=currencies)

    async def async_get_currencies(self) -> dict[str, str]:
        rates = await self.async_get_rates()
        return {code: item.name for code, item in rates.currencies.items()}
