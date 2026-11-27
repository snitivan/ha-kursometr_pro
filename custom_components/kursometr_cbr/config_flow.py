"""Мастер настройки Курсометра.

Множественное добавление инструментов (валют и драгметаллов) реализовано
через стандартный механизм Home Assistant: первый выбранный инструмент
создаётся текущим потоком настройки, а для каждого последующего мастер
показывает шаг подтверждения ``more_info`` — официальный способ создавать
несколько config entries из одного потока (без фоновых задач и внутренних
API).

Выбор разделён на категории «Валюты» и «Драгметаллы»: нативных групп в
селекторе HA нет, поэтому каждый блок подписан строкой-заголовком.
Заголовки не являются инструментами и отбрасываются при сохранении.
"""

from __future__ import annotations

from types import MappingProxyType

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
    """Список с множественным выбором: сначала валюты, затем металлы."""
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
    """Человекочитаемое имя инструмента для шага подтверждения."""
    if code in METAL_NAMES:
        return f"{code} — {METAL_NAMES[code]}"
    name = currencies.get(code) or FALLBACK_CURRENCIES.get(code)
    return f"{code} — {name}" if name else code


def _clean_selection(raw) -> list[str]:
    """Реальные коды из выбора пользователя (заголовки отбрасываются)."""
    items = raw if isinstance(raw, (list, tuple)) else [raw]
    codes: list[str] = []
    for item in items:
        code = str(item).strip().upper()
        if code in HEADER_VALUES or not code:
            continue
        if code not in codes:
            codes.append(code)
    return codes


class KursometrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        # Очередь инструментов, оставшихся после создания первой записи
        # (см. async_step_user / async_step_more_info).
        self._pending: list[tuple[str, int]] = []
        # Имена инструментов для шага подтверждения.
        self._names: dict[str, str] = {}
        # Уже созданные этим потоком записи (кроме финальной).
        self._created: list[ConfigEntry] = []

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
        errors: dict[str, str] = {}

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
                    for e in self._async_current_entries()
                }
                new_codes = [c for c in codes if c not in configured]
                if not new_codes:
                    # Все отмеченные инструменты уже измеряются.
                    return self.async_abort(reason="already_configured")
                # Первый инструмент создаётся сразу и добавляется в HA;
                # очередь остальных обрабатывает шаг more_info.
                first = new_codes[0]
                await self.async_set_unique_id(f"{DOMAIN}_{first.lower()}")
                self._abort_if_unique_id_configured()
                self._pending = [(c, interval) for c in new_codes[1:]]
                if self._pending:
                    await self._async_add_immediate(first, interval)
                    return await self.async_step_more_info()
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

    async def _async_add_immediate(self, currency: str, interval: int) -> None:
        """Добавляет запись сразу, не завершая поток.

        При множественном выборе первый инструмент должен появиться в HA
        немедленно (иначе пользователь увидит «ничего не создалось» после
        закрытия мастера). Финальная запись создаётся стандартным
        async_create_entry на последнем шаге more_info.
        """
        from homeassistant.config_entries import ConfigEntry  # local re-import

        unique_id = f"{DOMAIN}_{currency.lower()}"
        if self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, unique_id
        ):
            return
        entry = ConfigEntry(
            data={CONF_CURRENCY: currency, CONF_SCAN_INTERVAL: interval},
            discovery_keys=MappingProxyType({}),
            domain=DOMAIN,
            minor_version=1,
            options={},
            source=self.source,
            title=f"{currency} → RUB",
            unique_id=unique_id,
            version=self.VERSION,
        )
        await self.hass.config_entries.async_add(entry)
        self._created.append(entry)

    async def async_step_more_info(self, user_input=None) -> FlowResult:
        """Подтверждение добавления следующего выбранного инструмента.

        Промежуточные подтверждения создают записи напрямую (мастер уже
        показал их пользователю в исходной форме), финальное — отдаёт
        обычный async_create_entry, завершающий поток.
        """
        currency, interval = self._pending[0]

        if user_input is not None:
            self._pending.pop(0)
            if self._pending:
                await self._async_add_immediate(currency, interval)
                return await self.async_step_more_info()
            return self._create_entry(currency, interval)

        return self.async_show_form(
            step_id="more_info",
            description_placeholders={
                "currency": _label_of(currency, self._names),
                "left": str(len(self._pending) - 1),
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
                changed_currency = (
                    currency
                    != (self.config_entry.data.get(CONF_CURRENCY) or "").upper()
                )
                new_data = {**self.config_entry.data, CONF_CURRENCY: currency}
                new_options = {
                    **self.config_entry.options,
                    CONF_SCAN_INTERVAL: interval,
                }
                new_unique_id = (
                    f"{DOMAIN}_{currency.lower()}"
                    if changed_currency
                    else self.config_entry.unique_id
                )
                new_title = (
                    f"{currency} → RUB"
                    if changed_currency
                    else self.config_entry.title
                )
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    unique_id=new_unique_id,
                    title=new_title,
                    data=new_data,
                    options=new_options,
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
                    vol.Required(
                        CONF_SCAN_INTERVAL, default=interval
                    ): _interval_selector(),
                }
            ),
            errors=errors,
        )
