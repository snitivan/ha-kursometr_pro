import threading

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


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
