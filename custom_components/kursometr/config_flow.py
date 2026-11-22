"""Мастер настройки Курсометра."""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
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


def _schema(currencies: dict[str, str], default: int) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_CURRENCY): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value=code, label=f"{code} — {name}")
                        for code, name in sorted(currencies.items())
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                    sort=False,
                )
            ),
            vol.Required(CONF_SCAN_INTERVAL, default=default): _interval_selector(),
        }
    )


class KursometrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            currency = str(user_input[CONF_CURRENCY]).upper()
            interval = int(user_input[CONF_SCAN_INTERVAL])
            if MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL:
                await self.async_set_unique_id(f"{DOMAIN}_{currency.lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"{currency} → RUB",
                    data={
                        CONF_CURRENCY: currency,
                        CONF_SCAN_INTERVAL: interval,
                    },
                )
            errors[CONF_SCAN_INTERVAL] = "invalid_interval"

        try:
            currencies = await CbrXmlApi(
                async_get_clientsession(self.hass)
            ).async_get_currency_options()
        except XmlApiError:
            currencies = FALLBACK_CURRENCIES

        # Металлы добавляем всегда, даже если сервис ЦБ недоступен:
        # они не зависят от ответа валютного списка.
        currencies.update(METAL_NAMES)

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(currencies, DEFAULT_SCAN_INTERVAL),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return KursometrOptionsFlow()


class KursometrOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            interval = int(user_input[CONF_SCAN_INTERVAL])
            if MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL:
                return self.async_create_entry(
                    title="", data={CONF_SCAN_INTERVAL: interval}
                )
            errors[CONF_SCAN_INTERVAL] = "invalid_interval"

        interval = int(
            self.config_entry.options.get(
                CONF_SCAN_INTERVAL,
                self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
            )
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_SCAN_INTERVAL, default=interval): _interval_selector()}
            ),
            errors=errors,
        )
