"""HA-dependent tests — require pytest-homeassistant-custom-component.

Run on Linux / CI:  pytest tests/ha
(The lightweight `pytest` run at the repo root ignores this directory.)
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# make `custom_components.hikvision_access...` importable from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# HA's camera component hard-imports the native turbojpeg binding, which isn't
# available in the CI test env; a stub is enough for a load test.
sys.modules.setdefault("turbojpeg", MagicMock())


@pytest.fixture(autouse=True)
def _auto_enable_custom_integrations(enable_custom_integrations):
    yield
