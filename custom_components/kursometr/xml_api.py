"""Клиент официального SOAP-сервиса Банка России (DailyInfo.asmx).

Сервис отдаёт курсы через XML-методы:
* ``GetLatestDate`` — последняя дата, на которую ЦБ установил курсы;
* ``GetCursOnDateXML`` — валютные курсы на указанную дату;
* ``DragMetDynamicXML`` — учётные цены драгоценных металлов
  (1 — золото, 2 — серебро, 3 — платина, 4 — палладий) за период.

Ответы имеют формат DataSet-подобного XML без пространств имён, поэтому
парсинг выполняется стандартной библиотекой ``xml.etree.ElementTree`` —
никаких дополнительных зависимостей не требуется.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from xml.etree import ElementTree as ET

import aiohttp

from .const import CBR_SOAP_URL, METAL_GOLD_CODE, METAL_SILVER_CODE, METALS_BY_CODE

SOAP_NAMESPACE = "http://web.cbr.ru/"


class XmlApiError(Exception):
    """Базовая ошибка SOAP-клиента."""


class XmlConnectionError(XmlApiError):
    """Сеть недоступна или сервис вернул транспортную ошибку."""


class XmlInvalidResponseError(XmlApiError):
    """Сервис ответил, но содержимое не распознано."""


@dataclass
class MetalRate:
    code: str
    name: str
    unit: str
    price: float


@dataclass
class DailyRates:
    date: str
    currencies: dict[str, tuple[str, int, float]]
    metals: dict[str, MetalRate]


def _envelope(method: str, body_xml: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><{method} xmlns="{SOAP_NAMESPACE}">{body_xml}</{method}>'
        "</soap:Body></soap:Envelope>"
    )


def _parse_date(value: str) -> str:
    """Превращает ``20261120`` или ISO-строку в ``YYYY-MM-DD``."""
    value = (value or "").strip()
    if len(value) >= 8 and value[:8].isdigit():
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value[:10]


def _local_date(value: str) -> str:
    """Дата из ISO-строки со смещением (``+03:00`` отбрасывается).

    Смещение в DragMetDynamicXML — московское, дата уже локальная;
    оставлять её с суффиксом времени нельзя: сравнение строк дат
    «2026-11-19T00:00:00+03:00» и «2026-11-20T00:00:00+03:00» корректно
    только при одинаковой длине префикса до T.
    """
    return _parse_date(value)


class CbrXmlApi:
    def __init__(self, session: aiohttp.ClientSession, url: str = CBR_SOAP_URL):
        self._session = session
        self._url = url

    async def _call(self, method: str, body_xml: str = "") -> ET.Element:
        envelope = _envelope(method, body_xml)
        try:
            async with self._session.post(
                self._url,
                data=envelope.encode("utf-8"),
                headers={
                    "Content-Type": "text/xml; charset=utf-8",
                    "SOAPAction": f"{SOAP_NAMESPACE}{method}",
                },
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                text = await response.text()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise XmlConnectionError(str(err)) from err

        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise XmlInvalidResponseError("ЦБ прислал не XML") from err

        fault = root.find(".//{http://schemas.xmlsoap.org/soap/envelope/}Fault")
        if fault is not None:
            raise XmlInvalidResponseError("SOAP Fault от сервиса ЦБ")
        return root

    async def async_get_latest_date(self) -> str:
        root = await self._call("GetLatestDate")
        node = root.find(f".//{{{SOAP_NAMESPACE}}}GetLatestDateResult")
        if node is None or not (node.text or "").strip():
            raise XmlInvalidResponseError("Нет даты в GetLatestDate")
        return _parse_date(node.text)

    async def async_get_currencies_on_date(self, day: date) -> dict:
        body = f"<On_date>{day.isoformat()}T00:00:00</On_date>"
        root = await self._call("GetCursOnDateXML", body)
        items: dict[str, tuple[str, int, float]] = {}
        for valute in root.iter("ValuteCursOnDate"):
            code = (valute.findtext("VchCode") or "").strip()
            name = (valute.findtext("Vname") or "").strip()
            try:
                nominal = int(float(valute.findtext("Vnom") or "0"))
                rate = float((valute.findtext("VunitRate") or "").replace(",", "."))
            except ValueError:
                continue
            if not code or nominal <= 0 or rate <= 0:
                continue
            items[code] = (name, nominal, rate)
        if not items:
            raise XmlInvalidResponseError("В ответе нет курсов валют")
        return items

    async def async_get_metals_range(self, last_day: date, days: int = 7) -> dict:
        first_day = last_day - timedelta(days=days)
        body = (
            f"<fromDate>{first_day.isoformat()}T00:00:00</fromDate>"
            f"<ToDate>{last_day.isoformat()}T00:00:00</ToDate>"
        )
        root = await self._call("DragMetDynamicXML", body)
        # CodMet сервиса ("1", "2") отображаем в собственные коды GOLD/SILVER.
        special_by_codmet = {
            "1": METAL_GOLD_CODE,
            "2": METAL_SILVER_CODE,
        }
        best: dict[str, tuple[str, float]] = {}
        for row in root.iter("DrgMet"):
            codmet = (row.findtext("CodMet") or "").strip()
            code = special_by_codmet.get(codmet)
            if code is None:
                continue
            raw_date = _local_date(row.findtext("DateMet") or "")
            try:
                price = float((row.findtext("price") or "").replace(",", "."))
            except ValueError:
                continue
            if price <= 0:
                continue
            prev = best.get(code)
            if prev is None or raw_date > prev[0]:
                best[code] = (raw_date, price)
        if not best:
            raise XmlInvalidResponseError("В ответе нет цен металлов")
        result = {}
        for codmet, code in special_by_codmet.items():
            if code not in best:
                continue
            _, price = best[code]
            name, unit = METALS_BY_CODE[codmet]
            result[code] = MetalRate(code=code, name=name, unit=unit, price=price)
        return result

    async def async_get_daily_rates(self) -> DailyRates:
        latest = await self.async_get_latest_date()
        try:
            day = date.fromisoformat(latest)
        except ValueError as err:
            raise XmlInvalidResponseError(f"Непонятная дата: {latest!r}") from err

        currencies = await self.async_get_currencies_on_date(day)
        try:
            metals = await self.async_get_metals_range(day)
        except XmlApiError:
            # Металлы — гость необязательный: без золота и серебра
            # интеграция продолжает работать как раньше.
            metals = {}

        return DailyRates(date=latest, currencies=currencies, metals=metals)

    async def async_get_currency_options(self) -> dict[str, str]:
        """Валютный список для мастера настройки (без металлов).

        Дешевле ``async_get_daily_rates``: только последняя дата и курсы
        валют — металлы в селекторе статичны и добавляются отдельно.
        """
        latest = await self.async_get_latest_date()
        try:
            day = date.fromisoformat(latest)
        except ValueError as err:
            raise XmlInvalidResponseError(f"Непонятная дата: {latest!r}") from err
        currencies = await self.async_get_currencies_on_date(day)
        return {code: name for code, (name, _, _) in currencies.items()}

    async def async_get_options(self) -> dict[str, str]:
        """Список того, что можно измерить: валюты + золото и серебро."""
        rates = await self.async_get_daily_rates()
        options = {code: name for code, (name, _, _) in rates.currencies.items()}
        for special in (METAL_GOLD_CODE, METAL_SILVER_CODE):
            metal = rates.metals.get(special)
            if metal is not None:
                options[special] = metal.name
        return options
