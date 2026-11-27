import functools
import socket
import threading

import aiohttp.resolver
import homeassistant.helpers.aiohttp_client as _ha_client
import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


# В CI-окружении установлен pycares 5.x, чей Channel.getaddrinfo() принимает
# callback только именованным аргументом, тогда как aiodns 3.2 передаёт его
# позиционно — любой фактический HTTP-запрос падает с TypeError. Home Assistant
# конструирует коннекторы с ``resolver=AsyncResolver()`` напрямую (имя импортиро-
# вано в модуль aiohttp_client), поэтому подменяем именно его на потоковый
# ThreadedResolver: тесты не должны ходить в сеть, а DNS будет блокирован
# фикстурой no_network ниже.
aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver


class _SafeResolver(aiohttp.resolver.ThreadedResolver):
    """ThreadedResolver без выхода в реальную сеть."""

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if str(host) in ("127.0.0.1", "localhost", "::1"):
            return await super().resolve(host, port, family)
        raise OSError(f"Network access disabled in tests (host={host})")


_ha_client.AsyncResolver = _SafeResolver


@pytest.fixture(autouse=True)
def no_network(event_loop):
    """Запрещаем тестам реальные сетевые обращения (кроме localhost)."""

    real_getaddrinfo = socket.getaddrinfo

    def guard(host, port, *args, **kwargs):
        if str(host) not in ("127.0.0.1", "localhost", "::1"):
            raise OSError("Network access disabled in tests")
        return real_getaddrinfo(host, port, *args, **kwargs)

    socket.getaddrinfo = functools.wraps(real_getaddrinfo)(guard)
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# aiohttp/aiodns (pycares >= 4.7) при первом же DNS-запросе лениво поднимает
# демон-поток ``Thread-N (_run_safe_shutdown_loop)``, живущий до конца процесса.
# Проверка verify_cleanup из pytest-homeassistant-custom-component принимает его
# за «нить, оставшуюся после теста» и роняет teardown. Поток может стартовать в
# любой момент — во время теста, teardown или между тестами, поэтому заглушку
# ``threading.enumerate`` не снимаем вовсе: фильтрация безвредна для остальных
# проверок (реальные утечки нитей по-прежнему видны).
_original_enumerate = threading.enumerate


def _filtered_enumerate():
    return [
        t for t in _original_enumerate() if "_run_safe_shutdown_loop" not in t.name
    ]


threading.enumerate = _filtered_enumerate
