from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kursometr.api import CbrConnectionError
from custom_components.kursometr.const import CONF_CURRENCY, DOMAIN

CURRENCIES = {"USD": "Доллар США", "EUR": "Евро", "JPY": "Иена"}


async def test_user_flow_creates_entry(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrApi.async_get_currencies",
        return_value=CURRENCIES,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.kursometr.config_flow.CbrApi.async_get_currencies",
        return_value=CURRENCIES,
    ), patch("custom_components.kursometr.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "USD → RUB"
    assert result["data"] == {CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600}
    assert result["result"].unique_id == "kursometr_usd"


async def test_duplicate_currency_is_aborted(hass):
    MockConfigEntry(
        domain=DOMAIN,
        unique_id="kursometr_usd",
        data={CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600},
    ).add_to_hass(hass)

    with patch(
        "custom_components.kursometr.config_flow.CbrApi.async_get_currencies",
        return_value=CURRENCIES,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CURRENCY: "USD", CONF_SCAN_INTERVAL: 3600},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_connection_error_shows_fallback_form(hass):
    with patch(
        "custom_components.kursometr.config_flow.CbrApi.async_get_currencies",
        side_effect=CbrConnectionError("offline"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_options_flow_updates_interval(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="kursometr_eur",
        data={CONF_CURRENCY: "EUR", CONF_SCAN_INTERVAL: 3600},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 120}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 120}
