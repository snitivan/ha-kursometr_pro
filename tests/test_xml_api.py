"""Тесты клиента официального XML-сервиса ЦБ РФ."""

from datetime import date
from xml.etree import ElementTree as ET

import pytest

from custom_components.kursometr.xml_api import (
    CbrXmlApi,
    XmlConnectionError,
    XmlInvalidResponseError,
)

LATEST_DATE_XML = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetLatestDateResponse xmlns="http://web.cbr.ru/">
      <GetLatestDateResult>20261120</GetLatestDateResult>
    </GetLatestDateResponse>
  </soap:Body>
</soap:Envelope>"""

CURS_XML = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <GetCursOnDateXMLResponse xmlns="http://web.cbr.ru/">
      <GetCursOnDateXMLResult>
        <ValuteData OnDate="20261120" xmlns="">
          <ValuteCursOnDate>
            <Vname>Доллар США</Vname><Vnom>1</Vnom><Vcurs>82.2281</Vcurs>
            <Vcode>840</Vcode><VchCode>USD</VchCode><VunitRate>82.2281</VunitRate>
          </ValuteCursOnDate>
          <ValuteCursOnDate>
            <Vname>Иена</Vname><Vnom>100</Vnom><Vcurs>51.5246</Vcurs>
            <Vcode>392</Vcode><VchCode>JPY</VchCode><VunitRate>0.515246</VunitRate>
          </ValuteCursOnDate>
          <ValuteCursOnDate>
            <Vname>Бракованная</Vname><Vnom>0</Vnom><Vcurs>1</Vcurs>
            <Vcode>000</Vcode><VchCode>BROKEN</VchCode><VunitRate>0</VunitRate>
          </ValuteCursOnDate>
        </ValuteData>
      </GetCursOnDateXMLResult>
    </GetCursOnDateXMLResponse>
  </soap:Body>
</soap:Envelope>"""

DRAG_MET_XML = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <DragMetDynamicXMLResponse xmlns="http://web.cbr.ru/">
      <DragMetDynamicXMLResult>
        <DragMetall xmlns="">
          <DrgMet><DateMet>2026-11-19T00:00:00+03:00</DateMet>
            <CodMet>1</CodMet><price>10774.3200</price></DrgMet>
          <DrgMet><DateMet>2026-11-19T00:00:00+03:00</DateMet>
            <CodMet>2</CodMet><price>138.4000</price></DrgMet>
          <DrgMet><DateMet>2026-11-20T00:00:00+03:00</DateMet>
            <CodMet>1</CodMet><price>10926.5100</price></DrgMet>
          <DrgMet><DateMet>2026-11-20T00:00:00+03:00</DateMet>
            <CodMet>2</CodMet><price>139.6700</price></DrgMet>
          <DrgMet><DateMet>2026-11-20T00:00:00+03:00</DateMet>
            <CodMet>3</CodMet><price>4141.8300</price></DrgMet>
        </DragMetall>
      </DragMetDynamicXMLResult>
    </DragMetDynamicXMLResponse>
  </soap:Body>
</soap:Envelope>"""

SOAP_FAULT_XML = """<?xml version="1.0" encoding="utf-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
  <soap:Body>
    <soap:Fault><faultstring>boom</faultstring></soap:Fault>
  </soap:Body>
</soap:Envelope>"""


class FakeResponse:
    def __init__(self, text):
        self._text = text

    def raise_for_status(self):
        pass

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class FakeSession:
    """Возвращает заготовленные XML по имени SOAP-метода."""

    def __init__(self, responses, error=None):
        self.responses = responses
        self.error = error
        self.requests = []

    def post(self, url, data=None, headers=None, timeout=None):
        if self.error is not None:
            raise self.error
        self.requests.append((url, data.decode("utf-8"), headers["SOAPAction"]))
        method = data.decode("utf-8")
        for name, payload in self.responses.items():
            if f"<{name} " in method:
                return FakeResponse(payload)
        return FakeResponse("<html>не expected</html>")


def make_api(**responses):
    return CbrXmlApi(FakeSession(responses))


async def test_get_latest_date_parses_compact_format():
    api = make_api(GetLatestDate=LATEST_DATE_XML)
    assert await api.async_get_latest_date() == "2026-11-20"


async def test_currencies_on_date_normalizes_nominal_and_rate():
    api = make_api(GetCursOnDateXML=CURS_XML)
    items = await api.async_get_currencies_on_date(date(2026, 11, 20))

    assert items["USD"] == ("Доллар США", 1, pytest.approx(82.2281))
    assert items["JPY"] == ("Иена", 100, pytest.approx(0.515246))
    assert "BROKEN" not in items


async def test_metals_pick_latest_row_per_code():
    api = make_api(DragMetDynamicXML=DRAG_MET_XML)
    metals = await api.async_get_metals_range(date(2026, 11, 20))

    assert set(metals) == {"GOLD", "SILVER"}
    assert metals["GOLD"].price == pytest.approx(10926.51)
    assert metals["GOLD"].unit == "RUB/g"
    assert metals["SILVER"].price == pytest.approx(139.67)


async def test_daily_rates_combines_all_calls():
    api = make_api(
        GetLatestDate=LATEST_DATE_XML,
        GetCursOnDateXML=CURS_XML,
        DragMetDynamicXML=DRAG_MET_XML,
    )
    rates = await api.async_get_daily_rates()

    assert rates.date == "2026-11-20"
    assert rates.currencies["USD"][2] == pytest.approx(82.2281)
    assert rates.metals["GOLD"].price == pytest.approx(10926.51)

    options = await api.async_get_options()
    assert options["USD"] == "Доллар США"
    assert options["GOLD"] == "Золото"
    assert options["SILVER"] == "Серебро"


async def test_daily_rates_survives_missing_metals():
    api = make_api(
        GetLatestDate=LATEST_DATE_XML,
        GetCursOnDateXML=CURS_XML,
        DragMetDynamicXML="<html>error</html>",
    )
    rates = await api.async_get_daily_rates()

    assert rates.metals == {}
    assert "USD" in rates.currencies


async def test_soap_fault_raises_invalid_response():
    api = make_api(GetLatestDate=SOAP_FAULT_XML)
    with pytest.raises(XmlInvalidResponseError):
        await api.async_get_latest_date()


async def test_connection_error_is_wrapped():
    import asyncio

    session = FakeSession({}, error=asyncio.TimeoutError())
    api = CbrXmlApi(session)
    with pytest.raises(XmlConnectionError):
        await api.async_get_latest_date()


async def test_empty_curs_raises():
    api = make_api(
        GetCursOnDateXML="""<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
        <soap:Body><GetCursOnDateXMLResponse xmlns="http://web.cbr.ru/">
        <GetCursOnDateXMLResult><ValuteData xmlns="" /></GetCursOnDateXMLResult>
        </GetCursOnDateXMLResponse></soap:Body></soap:Envelope>"""
    )
    with pytest.raises(XmlInvalidResponseError):
        await api.async_get_currencies_on_date(date(2026, 11, 20))


async def test_request_envelope_contains_method_and_namespace():
    session = FakeSession({"GetLatestDate": LATEST_DATE_XML})
    api = CbrXmlApi(session)
    await api.async_get_latest_date()

    url, body, soap_action = session.requests[0]
    assert url.endswith("/DailyInfo.asmx")
    assert 'xmlns="http://web.cbr.ru/"' in body
    assert soap_action == "http://web.cbr.ru/GetLatestDate"
    # ответ распарсился без ошибок — XML валиден
    ET.fromstring(LATEST_DATE_XML)
