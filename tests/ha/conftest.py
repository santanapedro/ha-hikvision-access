"""HA-dependent tests — require pytest-homeassistant-custom-component.

Run on Linux / CI:  pytest tests/ha -p pytest_homeassistant_custom_component
(The lightweight `pytest` run at the repo root ignores this directory.)
"""
import pytest


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    yield
