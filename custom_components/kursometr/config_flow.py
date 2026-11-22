"""Мастер настройки Курсометра.

Выбор инструмента реализован списком с множественным выбором и
заголовками-разделителями («Валюты» / «Драгметаллы»): нативных групп в
селекторе HA нет, а заголовки отбрасываются при сохранении — создать
запись можно только выбрав ровно один реальный код.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_CURRENCY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    METAL_CODES,
    METAL_NAMES,
    MIN_SCAN_INTERVAL,
)
from .xml_api import CbrXmlApi, XmlApiError

FALLBACK_CURRENCIES = {
    "USD": "Доллар США",
    "EUR": "Евро",
    "GBP": "Фунт стерлингов",
    "CNY": "Юань",
    "JPY": "Иена",
}


def _interval_selector() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=MIN_SCAN_INTERVAL,
            max=MAX_SCAN_INTERVAL,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
            unit_of_measurement="s",
        )
    )


def _currency_selector(currencies: dict[str, str]) -> selector.SelectSelector:
    """Выпадающий список из валют и металлов с разделением по категориям.

    В режиме ``dropdown`` HA скрёт группы, поэтому выбор делается
    списком с множественным выбором: сначала идут валюты, затем
    драгметаллы — каждый блок подписан своей строкой-заголовком.
    Выбрать нужно ровно один пункт.
    """
    options: list[dict] = [
        {"value": "__header_currencies__", "label": "— Валюты —"}
    ]
    options.extend(
        {"value": code, "label": f"{code} — {name}"}
        for code, name in sorted(currencies.items())
    )
    options.append({"value": "__header_metals__", "label": "— Драгметаллы —"})
    options.extend(
        {"value": code, "label": f"{code} — {METAL_NAMES[code]}"}
        for code in METAL_CODES
    )
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=selector.SelectSelectorMode.LIST,
            sort=False,
        )
    )


HEADER_VALUES = frozenset({"__header_currencies__", "__header_metals__"})


def _clean_selection(raw) -> tuple[list[str], list[str]]:
    """Разделяет выбор пользователя на реальные коды и заголовки-разделители."""
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    codes = [str(item).upper() for item in items if str(item) not in HEADER_VALUES]
    headers = [str(item) for item in items if str(item) in HEADER_VALUES]
    return codes, headers


class KursometrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            codes, _headers = _clean_selection(user_input[CONF_CURRENCY])
            interval = int(user_input[CONF_SCAN_INTERVAL])
            if len(codes) != 1:
                errors[CONF_CURRENCY] = "invalid_selection"
            elif not MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "invalid_interval"
            else:
                currency = codes[0]
                await self.async_set_unique_id(f"{DOMAIN}_{currency.lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{currency} → RUB",
                    data={
                        CONF_CURRENCY: currency,
                        CONF_SCAN_INTERVAL: interval,
                    },
                )

        try:
            currencies = await CbrXmlApi(
                async_get_clientsession(self.hass)
            ).async_get_currency_options()
        except XmlApiError:
            currencies = FALLBACK_CURRENCIES

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CURRENCY): _currency_selector(currencies),
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                    ): _interval_selector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return KursometrOptionsFlow(config_entry)


class KursometrOptionsFlow(OptionsFlowWithConfigEntry):
    """Настройки элемента: интервал опроса и смена инструмента."""

    async def async_step_init(self, user_input=None) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            codes, headers = _clean_selection(user_input[CONF_CURRENCY])
            interval = int(user_input[CONF_SCAN_INTERVAL])
            if len(codes) != 1:
                errors[CONF_CURRENCY] = "invalid_selection"
            elif not MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "invalid_interval"
            else:
                currency = codes[0]
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    unique_id=f"{DOMAIN}_{currency.lower()}",
                    title=f"{currency} → RUB",
                    data={**self.config_entry.data, CONF_CURRENCY: currency},
                    options={
                        **self.config_entry.options,
                        CONF_SCAN_INTERVAL: interval,
                    },
                )
                return self.async_create_entry(title="", data={})

        selected = [self.config_entry.data[CONF_CURRENCY]]
        interval = int(
            self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        )

        try:
            currencies = await CbrXmlApi(
                async_get_clientsession(self.hass)
            ).async_get_currency_options()
        except XmlApiError:
            currencies = FALLBACK_CURRENCIES

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_CURRENCY, default=selected): _currency_selector(
                        currencies
                    ),
                    vol.Required(CONF_SCAN_INTERVAL, default=interval): _interval_selector(),
                }
            ),
            errors=errors,
        )
