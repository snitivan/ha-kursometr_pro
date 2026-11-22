import threading

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def ignore_pycares_shutdown_thread():
    """Не считать фоновый поток pycares утечкой.

    aiohttp/aiodns при создании клиента поднимает ленивый демон-поток
    ``Thread-N (_run_safe_shutdown_loop)`` (pycares >= 4.7), который живёт до
    конца процесса. Проверка verify_cleanup из pytest-homeassistant-custom-
    component ошибочно принимает его за оставшуюся после теста нить.
    """
    original_enumerate = threading.enumerate

    def enumerate():
        return [
            t
            for t in original_enumerate()
            if "_run_safe_shutdown_loop" not in t.name
        ]

    threading.enumerate = enumerate
    try:
        yield
    finally:
        threading.enumerate = original_enumerate
