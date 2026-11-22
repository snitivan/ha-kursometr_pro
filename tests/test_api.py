from unittest.mock import patch

import pytest

from custom_components.kursometr.xml_api import (
    CbrXmlApi,
    DailyRates,
    MetalRate,
    XmlInvalidResponseError,
)

RATES = DailyRates(
    date="2026-11-20",
    currencies={
        "USD": ("Доллар США", 1, 82.2281),
        "JPY": ("Иена", 100, 0.515246),
    },
    metals={
        "GOLD": MetalRate("GOLD", "Золото", "RUB/g", 10926.51),
        "SILVER": MetalRate("SILVER", "Серебро", "RUB/g", 139.67),
    },
)


def make_api():
    return CbrXmlApi(None)


async def test_async_get_options_includes_metals():
    api = make_api()
    with patch.object(api, "async_get_daily_rates", return_value=RATES):
        options = await api.async_get_options()

    assert options == {
        "USD": "Доллар США",
        "JPY": "Иена",
        "GOLD": "Золото",
        "SILVER": "Серебро",
    }


async def test_missing_currency_raises():
    api = make_api()
    empty = DailyRates(date="2026-11-20", currencies={}, metals={})
    with patch.object(api, "async_get_daily_rates", return_value=empty):
        # пустой список опций допустим (валидны только металлы)
        assert await api.async_get_options() == {}

    # а координатор на незнакомом коде должен упасть с UpdateFailed
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from custom_components.kursometr.coordinator import KursometrCoordinator

    coordinator = KursometrCoordinator.__new__(KursometrCoordinator)
    coordinator.api = api
    coordinator.currency = "ZZZ"
    coordinator.is_metal = False
    with patch.object(api, "async_get_daily_rates", return_value=empty):
        with pytest.raises(UpdateFailed):
            await KursometrCoordinator._async_update_data(coordinator)


async def test_coordinator_metal_without_prices_raises():
    from homeassistant.helpers.update_coordinator import UpdateFailed
    from custom_components.kursometr.coordinator import KursometrCoordinator

    api = make_api()
    no_metals = DailyRates(date="2026-11-20", currencies={}, metals={})
    coordinator = KursometrCoordinator.__new__(KursometrCoordinator)
    coordinator.api = api
    coordinator.currency = "GOLD"
    coordinator.is_metal = True
    with patch.object(api, "async_get_daily_rates", return_value=no_metals):
        with pytest.raises(UpdateFailed):
            await KursometrCoordinator._async_update_data(coordinator)


async def test_empty_curs_on_date_raises():
    from custom_components.kursometr.xml_api import CbrXmlApi as _Api

    class EmptyCursSession:
        class Resp:
            def raise_for_status(self):
                pass

            async def text(self):
                return (
                    '<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                    '<soap:Body><GetCursOnDateXMLResponse xmlns="http://web.cbr.ru/">'
                    "<GetCursOnDateXMLResult><ValuteData /></GetCursOnDateXMLResult>"
                    "</GetCursOnDateXMLResponse></soap:Body></soap:Envelope>"
                )

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

        def post(self, *args, **kwargs):
            return self.Resp()

    api = _Api(EmptyCursSession())
    from datetime import date as _date

    with pytest.raises(XmlInvalidResponseError):
        await api.async_get_currencies_on_date(_date(2026, 11, 20))
