from unittest.mock import patch

import pytest
from homeassistant.const import CONF_SCAN_INTERVAL
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.kursometr_cbr.xml_api import DailyRates, MetalRate
from custom_components.kursometr_cbr.const import ATTR_NOMINAL, CONF_CURRENCY, DOMAIN

RATES = DailyRates(
    date="2026-11-20",
    currencies={
        "USD": ("Доллар США", 1, 82.2281),
        "JPY": ("Иена", 100, 0.515246),
        "EUR": ("Евро", 1, 90.3605),
    },
    metals={
        "GOLD": MetalRate("GOLD", "Золото", "RUB/g", 10926.51),
        "SILVER": MetalRate("SILVER", "Серебро", "RUB/g", 139.67),
    },
)


def make_entry(currency="USD"):
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{currency.lower()}",
        title=f"{currency} → RUB",
        data={CONF_CURRENCY: currency, CONF_SCAN_INTERVAL: 3600},
    )


async def test_setup_creates_sensor_with_attributes(hass):
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr_cbr.xml_api.CbrXmlApi.async_get_daily_rates",
        return_value=RATES,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.entry_id in hass.data[DOMAIN]

    state = hass.states.get("sensor.kurs_usd")
    assert state is not None
    assert float(state.state) == pytest.approx(82.2281)
    assert state.attributes["unit_of_measurement"] == "RUB"
    assert state.attributes["device_class"] == "monetary"
    assert state.attributes["currency"] == "USD"
    assert state.attributes[ATTR_NOMINAL] == 1
    assert state.attributes["date"] == RATES.date
    assert state.attributes["icon"] == "mdi:currency-usd"

    jpy_entry = make_entry("JPY")
    jpy_entry.add_to_hass(hass)
    with patch(
        "custom_components.kursometr_cbr.xml_api.CbrXmlApi.async_get_daily_rates",
        return_value=RATES,
    ):
        assert await hass.config_entries.async_setup(jpy_entry.entry_id)
        await hass.async_block_till_done()

    jpy_state = hass.states.get("sensor.kurs_jpy")
    assert jpy_state is not None
    assert float(jpy_state.state) == pytest.approx(0.515246)
    assert jpy_state.attributes[ATTR_NOMINAL] == 100


async def test_setup_gold_sensor_uses_metal_price(hass):
    entry = make_entry("GOLD")
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr_cbr.xml_api.CbrXmlApi.async_get_daily_rates",
        return_value=RATES,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.kurs_gold")
    assert state is not None
    assert float(state.state) == pytest.approx(10926.51)
    assert state.attributes["unit_of_measurement"] == "RUB/g"
    assert state.attributes["currency"] == "GOLD"
    assert state.attributes["currency_name"] == "Золото"
    assert state.attributes["icon"] == "mdi:gold"


async def test_unload_removes_coordinator(hass):
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr_cbr.xml_api.CbrXmlApi.async_get_daily_rates",
        return_value=RATES,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_options_change_reloads_coordinator(hass):
    entry = make_entry()
    entry.add_to_hass(hass)

    with patch(
        "custom_components.kursometr_cbr.xml_api.CbrXmlApi.async_get_daily_rates",
        return_value=RATES,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        hass.config_entries.async_update_entry(
            entry, options={CONF_SCAN_INTERVAL: 60}
        )
        await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.update_interval.total_seconds() == 60
