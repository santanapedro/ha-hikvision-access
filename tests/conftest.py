"""Register the component as an importable package WITHOUT running its
Home Assistant __init__.py, so the pure-logic modules (api, event_mapper,
capabilities, models, const, exceptions) can be unit-tested with plain pytest.
"""

import sys
import types
from pathlib import Path

_PKG = Path(__file__).resolve().parents[1] / "custom_components" / "hikvision_access"

if "hikvision_access" not in sys.modules:
    _mod = types.ModuleType("hikvision_access")
    _mod.__path__ = [str(_PKG)]
    sys.modules["hikvision_access"] = _mod

FIXTURES = Path(__file__).parent / "fixtures"
