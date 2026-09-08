"""Tiny helpers with no intra-package dependencies."""

from __future__ import annotations

import json
from typing import Any


def loads_hik(raw: bytes | str) -> Any:
    """json.loads for terminal replies.

    The DS-K1T342MWX sends correct UTF-8 (names like ``CÁSSIA`` arrive as the
    proper two-byte sequence). Latin-1 is kept only as a fallback for firmware
    that mislabels a Latin-1 body as UTF-8.
    """
    if isinstance(raw, str):
        return json.loads(raw)
    for enc in ("utf-8", "latin-1"):
        try:
            return json.loads(raw.decode(enc))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return json.loads(raw.decode("utf-8", errors="replace"))
