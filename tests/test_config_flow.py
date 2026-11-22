from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kursometr.xml_api import XmlConnectionError
from custom_components.kursometr.const import CONF_CURRENCY, DOMAIN

CURRENCIES = {"USD": "Доллар США", "EUR": "Евро", "JPY": "Иена"}


def _select_options(result):
    """Возвращает список options селектора currency из показанной формы."""
    for key, value in result["data_schema"].schema.items():
        if str(key) == CONF_CURRENCY:
            return value.config["options"]
    raise AssertionError("нет поля currency")


async def test_user_flow_creates_entry(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    # Категории видны как заголовки-разделители, металлы — всегда в списке.
    options = _select_options(result)
    values = [opt["value"] for opt in options]
    labels = {opt["value"]: opt["label"] for opt in options}
    assert "__header_currencies__" in values and "__header_metals__" in values
    assert values.index("__header_currencies__") < values.index("USD")
    assert values.index("__header_metals__") < values.index("GOLD")
    assert labels["GOLD"] == "GOLD — Золото"
    assert labels["SILVER"] == "SILVER — Серебро"

    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ), patch("custom_components.kursometr.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            # пользователь отмечает и заголовок категории, и валюту
            {CONF_CURRENCY: ["__header_currencies__", "USD"], CONF_SCAN_INTERVAL: 3600},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "USD → RUB"
    assert result["data"] == {CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600}
    assert result["result"].unique_id == "kursometr_usd"


async def test_selecting_only_header_is_error(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["__header_metals__"], CONF_SCAN_INTERVAL: 3600},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CURRENCY: "invalid_selection"}


async def test_selecting_two_instruments_is_error(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["USD", "GOLD"], CONF_SCAN_INTERVAL: 3600},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_CURRENCY: "invalid_selection"}


async def test_gold_can_be_selected(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ), patch("custom_components.kursometr.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["__header_metals__", "GOLD"], CONF_SCAN_INTERVAL: 3600},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CURRENCY] == "GOLD"


async def test_duplicate_currency_is_aborted(hass):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="kursometr_usd",
        data={CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600},
    ).add_to_hass(hass)

    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["USD"], CONF_SCAN_INTERVAL: 3600},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_connection_error_shows_fallback_form(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        side_effect=XmlConnectionError("offline"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    # Мастер не должен «ломаться» без ЦБ: показываем fallback-валюты
    # и металлы, предупреждение об ошибке связи больше не ставим.
    assert result["errors"] == {}
    values = [opt["value"] for opt in _select_options(result)]
    assert "USD" in values and "GOLD" in values and "SILVER" in values


async def test_options_flow_updates_interval(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="kursometr_eur",
        data={CONF_CURRENCY: "EUR", CONF_SCAN_INTERVAL: 3600},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    # Текущий инструмент отмечен по умолчанию
    default = next(
        k.default()
        for k, v in result["data_schema"].schema.items()
        if str(k) == CONF_CURRENCY
    )
    assert default == ["EUR"]

    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["EUR"], CONF_SCAN_INTERVAL: 120},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 120}
    assert entry.data[CONF_CURRENCY] == "EUR"


async def test_options_flow_changes_currency(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="kursometr_eur",
        data={CONF_CURRENCY: "EUR", CONF_SCAN_INTERVAL: 3600},
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr.config_flow.CbrXmlApi.async_get_currency_options",
        return_value=dict(CURRENCIES),
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: ["__header_metals__", "SILVER"], CONF_SCAN_INTERVAL: 900},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_CURRENCY] == "SILVER"
    assert entry.unique_id == "kursometr_silver"
    assert entry.title == "SILVER → RUB"
