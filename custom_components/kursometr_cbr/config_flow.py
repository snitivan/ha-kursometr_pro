"""Мастер настройки Курса ЦБ РФ.

Множественное добавление инструментов (валют и драгметаллов): первый
отмеченный инструмент создаётся стандартным ``async_create_entry``
(единственной записью, которую config flow умеет создавать), остальные
добавляются автоматически сразу после завершения мастера — напрямую через
``hass.config_entries.async_add``. Такой подход обходит ограничение HA
(один поток = одна запись) без служебных заглушек и шагов подтверждения.

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
        raw_code = str(item).strip()
        # Регистронезависимая проверка заголовков-разделителей: приводить к
        # upper() до сравнения нельзя, т.к. значения заголовков нижнего регистра.
        if raw_code.lower() in HEADER_VALUES or not raw_code:
            continue
        code = raw_code.upper()
        if code not in codes:
            codes.append(code)
    return codes


class KursometrConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        # Инструменты, добавляемые автоматически после завершения мастера.
        self._rest: list[tuple[str, int]] = []
        # Имена инструментов для человекочитаемых названий записей.
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
                # Первый инструмент — стандартная запись потока; остальные
                # будут добавлены сразу после её создания (см. _create_entry).
                first = new_codes[0]
                await self.async_set_unique_id(f"{DOMAIN}_{first.lower()}")
                self._abort_if_unique_id_configured()
                self._rest = [(c, interval) for c in new_codes[1:]]
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
        """Создаёт запись для первого инструмента и очередь на остальные.

        Оставшиеся выбранные инструменты добавляются автоматически сразу
        после завершения мастера (см. ``_async_add_rest``): config flow HA
        может создать только одну запись, но мы добавляем прочие напрямую
        через ``hass.config_entries.async_add``.
        """
        result = self.async_create_entry(
            title=f"{currency} → RUB",
            data={
                CONF_CURRENCY: currency,
                CONF_SCAN_INTERVAL: interval,
            },
        )
        if self._rest:
            rest = list(self._rest)
            self._rest = []

            async def _add_rest() -> None:
                for code, intr in rest:
                    await self._async_add_immediate(code, intr)

            self.hass.async_create_task(_add_rest())
        return result

    async def _async_add_immediate(self, currency: str, interval: int) -> None:
        """Добавляет config entry для одного инструмента вне потока.

        ``async_entry_for_domain_unique_id`` защищает от дублей, если запись
        с таким инструментом уже существует.
        """
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
            source=self.source or "user",
            title=f"{currency} → RUB",
            unique_id=unique_id,
            version=self.VERSION,
        )
        # Регистрируем запись в HA; async_add сам запускает её setup.
        await self.hass.config_entries.async_add(entry)

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
                )
                # async_create_entry в options-потоке сохраняет поле data как
                # entry.options; передаём его явно — иначе пустой {} затрёт
                # настройки при перезагрузке записи.
                return self.async_create_entry(title="", data=new_options)

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
