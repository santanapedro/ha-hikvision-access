"""HA-dependent tests — require pytest-homeassistant-custom-component.

Run on Linux / CI:  pytest tests/ha
(The lightweight `pytest` run at the repo root ignores this directory.)
"""

import sys
from pathlib import Path

# make `custom_components.hikvision_access...` importable from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    yield
