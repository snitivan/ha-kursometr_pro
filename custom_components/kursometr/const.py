"""Константы интеграции Курсометр."""

from __future__ import annotations

DOMAIN = "kursometr"
CONF_CURRENCY = "currency"

# Официальный SOAP-сервис Банка России (XML Web Service Daily Info).
CBR_SOAP_URL = "https://www.cbr.ru/DailyInfoWebServ/DailyInfo.asmx"

# Специальные коды драгоценных металлов (не ISO-4217, наши собственные).
METAL_GOLD_CODE = "GOLD"
METAL_SILVER_CODE = "SILVER"

# CodMet из DragMetDynamicXML -> (название, единица измерения)
METALS_BY_CODE = {
    "1": ("Золото", "RUB/g"),
    "2": ("Серебро", "RUB/g"),
}

# Специальный код -> его отображаемое имя.
METAL_NAMES = {
    METAL_GOLD_CODE: METALS_BY_CODE["1"][0],
    METAL_SILVER_CODE: METALS_BY_CODE["2"][0],
}

METAL_CODES = (METAL_GOLD_CODE, METAL_SILVER_CODE)

DEFAULT_SCAN_INTERVAL = 3600
MIN_SCAN_INTERVAL = 10
MAX_SCAN_INTERVAL = 86400

PLATFORMS = ["sensor"]

ATTR_CURRENCY = "currency"
ATTR_NAME = "currency_name"
ATTR_NOMINAL = "nominal"
ATTR_VALUE = "official_rate"
ATTR_DATE = "date"
