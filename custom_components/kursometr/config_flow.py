"""Мастер настройки Курсометра.

Выбор инструментов реализован списком с множественным выбором и
заголовками-разделителями («Валюты» / «Драгметаллы»): нативных групп в
селекторе HA нет, а заголовки отбрасываются при сохранении. Отметить
можно сразу несколько пунктов — на каждый создаётся отдельная запись
config entry (со своими сенсорами «текущий», «вчера», «завтра»).
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlowWithConfigEntry,
)
from homeassistant.const import CONF_SCAN_INTERVAL, SOURCE_USER
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
    Заголовки не являются инструментами и отбрасываются при сохранении.
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


class _AllConfigured(Exception):
    """Все выбранные инструменты уже настроены."""


def _clean_selection(raw) -> list[str]:
    """Реальные коды из выбора пользователя (заголовки-разделители отбрасываются)."""
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    codes: list[str] = []
    for item in items:
        code = str(item).upper()
        if code in HEADER_VALUES or not code:
            continue
        if code not in codes:
            codes.append(code)
    return codes


class KursometrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        # Инструменты, оставшиеся «в очереди» после создания первой записи
        # при множественном выборе (см. async_step_user).
        self._pending: list[tuple[str, int]] = []

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}

        if user_input is not None:
            codes = _clean_selection(user_input[CONF_CURRENCY])
            interval = int(user_input[CONF_SCAN_INTERVAL])
            if not codes:
                errors[CONF_CURRENCY] = "invalid_selection"
            elif not MIN_SCAN_INTERVAL <= interval <= MAX_SCAN_INTERVAL:
                errors[CONF_SCAN_INTERVAL] = "invalid_interval"
            else:
                configured = {
                    (e.data.get(CONF_CURRENCY) or "").upper()
                    for e in self._async_current_entries(include_ignore=False)
                }
                new_codes = [c for c in codes if c not in configured]
                if not new_codes:
                    errors[CONF_CURRENCY] = "all_configured"
                else:
                    # Первый инструмент создаём этим потоком; остальные
                    # добавляет шаг "more" — так можно отметить сразу
                    # несколько валют и металлов в одном окне мастера.
                    first = new_codes[0]
                    await self.async_set_unique_id(f"{DOMAIN}_{first.lower()}")
                    self._abort_if_unique_id_configured()
                    self._pending = [(c, interval) for c in new_codes[1:]]
                    result = self.async_create_entry(
                        title=f"{first} → RUB",
                        data={
                            CONF_CURRENCY: first,
                            CONF_SCAN_INTERVAL: interval,
                        },
                    )
                    if self._pending:
                        # Продолжение очереди запускаем фоном после того,
                        # как HA завершит текущий поток.
                        self.hass.async_create_task(self._async_process_pending())
                    return result

        try:
            currencies = await CbrXmlApi(
                async_get_clientsession(self.hass)
            ).async_get_currency_options()
        except XmlApiError:
            currencies = FALLBACK_CURRENCIES

        default_codes = (
            _clean_selection(user_input[CONF_CURRENCY])
            if user_input and CONF_CURRENCY in user_input
            else []
        )
        default_interval = (
            int(user_input[CONF_SCAN_INTERVAL])
            if user_input and CONF_SCAN_INTERVAL in user_input
            else DEFAULT_SCAN_INTERVAL
        )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_CURRENCY, default=default_codes
                    ): _currency_selector(currencies),
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=default_interval
                    ): _interval_selector(),
                }
            ),
            errors=errors,
        )

    async def _async_process_pending(self) -> None:
        """Создаёт config entries для инструментов, отмеченных дополнительно."""
        manager = self.hass.config_entries
        while self._pending:
            currency, interval = self._pending.pop(0)
            unique_id = f"{DOMAIN}_{currency.lower()}"
            if any(e.unique_id == unique_id for e in manager._async_entries):
                continue
            manager.add(
                ConfigEntry(
                    unique_id=unique_id,
                    domain=DOMAIN,
                    title=f"{currency} → RUB",
                    version=1,
                    source=SOURCE_USER,
                    data={CONF_CURRENCY: currency, CONF_SCAN_INTERVAL: interval},
                )
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
            codes = _clean_selection(user_input[CONF_CURRENCY])
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
