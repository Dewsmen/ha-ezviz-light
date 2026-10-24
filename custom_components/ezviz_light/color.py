"""EZVIZ color helpers: hex<->RGB conversion and raw feature-item lookup.

Standalone (no homeassistant/pyezvizapi imports) so it can be unit tested
without those dependencies installed.
"""

from __future__ import annotations

import json
from typing import Any


def hex_to_rgb(value: str) -> tuple[int, int, int] | None:
    """Parse an EZVIZ "#RRGGBB" hex string into an (r, g, b) tuple."""
    stripped = value.strip().lstrip("#")
    if len(stripped) != 6:
        return None
    try:
        return (
            int(stripped[0:2], 16),
            int(stripped[2:4], 16),
            int(stripped[4:6], 16),
        )
    except ValueError:
        return None


def rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    """Format an (r, g, b) tuple as the "#RRGGBB" hex string EZVIZ expects."""
    r, g, b = (max(0, min(255, channel)) for channel in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def raw_feature_item(data: dict[str, Any], item_key: str) -> dict[str, Any] | None:
    """Look up a feature item's full dict (dataValue, dataDesc, ...) by itemKey."""
    for item in data.get("featureItems") or []:
        if isinstance(item, dict) and item.get("itemKey") == item_key:
            return item
    return None


def raw_feature_value(data: dict[str, Any], item_key: str) -> Any:
    """Look up a feature item's dataValue by itemKey from a status dict's featureItems."""
    item = raw_feature_item(data, item_key)
    return item.get("dataValue") if item else None


def parse_data_desc(data_desc: Any) -> dict[str, Any]:
    """Parse a feature item's dataDesc (a JSON string) into a dict.

    Confirmed, e.g., for color_temperature: '{"range_from": 2700,
    "range_to": 6500, "interval": 10, "unit": "k"}'. Returns {} for
    anything that isn't valid JSON (dataDesc is sometimes an enum list,
    a plain string, or empty) - callers should treat missing keys as
    "unknown", not an error.
    """
    if not isinstance(data_desc, str):
        return {}
    try:
        parsed = json.loads(data_desc)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
