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

    Сначала идут валюты, затем драгметаллы — каждый блок подписан своей
    строкой-заголовком. Заголовки не являются инструментами и
    отбрасываются при сохранении.
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
            custom_value=False,
        )
    )


HEADER_VALUES = frozenset({"__header_currencies__", "__header_metals__"})


def _label_of(code: str, currencies: dict[str, str]) -> str:
    """Человекочитаемое имя инструмента для заголовка шага «more»."""
    if code in METAL_NAMES:
        return f"{code} — {METAL_NAMES[code]}"
    name = currencies.get(code)
    if name is None:
        # Краткий ISO-код без описания (например, AED) — ищем в fallback.
        name = FALLBACK_CURRENCIES.get(code, code)
    return f"{code} — {name}"


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
    supports_multiple_instances = True

    def __init__(self) -> None:
        # Инструменты, оставшиеся «в очереди» после создания первой записи
        # при множественном выборе (см. async_step_user / async_step_more).
        self._pending: list[tuple[str, int]] = []
        # Имена инструментов для шага "more" (если API был недоступен —
        # заполнятся fallback-списком).
        self._names: dict[str, str] = {}

    async def _async_names(self) -> dict[str, str]:
        """Валюты для селектора; при недоступности ЦБ — fallback-список."""
        try:
            currencies = await CbrXmlApi(
                async_get_clientsession(self.hass)
            ).async_get_currency_options()
        except XmlApiError:
            currencies = FALLBACK_CURRENCIES
        self._names = dict(currencies)
        return currencies

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
                    if self._pending:
                        return await self.async_step_more()
                    return self._create_entry(first, interval)

        currencies = await self._async_names()

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

    def _create_entry(self, currency: str, interval: int) -> FlowResult:
        """Создаёт config entry для одного инструмента."""
        return self.async_create_entry(
            title=f"{currency} → RUB",
            data={
                CONF_CURRENCY: currency,
                CONF_SCAN_INTERVAL: interval,
            },
        )

    async def async_step_more(self, user_input=None) -> FlowResult:
        """Продолжение мастера: добавляет оставшиеся отмеченные инструменты.

        Это официальный механизм HA для последовательного создания
        нескольких записей из одного потока — никаких внутренних API и
        фоновых задач.
        """
        pending_currency, pending_interval = self._pending[0]

        if user_input is not None:
            self._pending.pop(0)
            if self._pending:
                return await self.async_step_more()
            return self._create_entry(pending_currency, pending_interval)

        return self.async_show_form(
            step_id="more",
            description_placeholders={
                "currency": _label_of(pending_currency, self._names),
                "left": str(len(self._pending)),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> KursometrOptionsFlow:
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
