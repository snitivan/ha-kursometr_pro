"""Клиент курсов Банка России.

Раньше интеграция пользовалась неофициальным зеркалом cbr-xml-daily.ru
(JSON ``daily_json.js``). Теперь источником служит официальный XML
SOAP-сервис ЦБ РФ — реализация в ``xml_api.py``, а этот модуль оставлен
как совместимая обёртка с прежними именами.
"""

from __future__ import annotations

from .xml_api import (  # noqa: F401  (реэкспорт для совместимости)
    CbrXmlApi,
    DailyRates,
    MetalRate,
    XmlApiError,
    XmlConnectionError,
    XmlInvalidResponseError,
)

CbrApi = CbrXmlApi
CbrApiError = XmlApiError
CbrConnectionError = XmlConnectionError
CbrInvalidResponseError = XmlInvalidResponseError

__all__ = [
    "CbrApi",
    "CbrApiError",
    "CbrConnectionError",
    "CbrInvalidResponseError",
    "CbrXmlApi",
    "DailyRates",
    "MetalRate",
    "XmlApiError",
    "XmlConnectionError",
    "XmlInvalidResponseError",
]
