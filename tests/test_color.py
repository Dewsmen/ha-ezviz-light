"""Unit tests for EZVIZ color helpers (hex<->RGB, feature lookup).

Loaded by file path, same reasoning as test_brightness.py: color.py has no
imports, so this avoids running the integration's __init__.py (which needs
homeassistant/pyezvizapi installed).
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ezviz_light_color",
    Path(__file__).parent.parent / "custom_components" / "ezviz_light" / "color.py",
)
assert _SPEC is not None and _SPEC.loader is not None
color = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(color)

hex_to_rgb = color.hex_to_rgb
rgb_to_hex = color.rgb_to_hex
raw_feature_value = color.raw_feature_value


def test_hex_to_rgb_matches_live_device_sample() -> None:
    assert hex_to_rgb("#43FF49") == (0x43, 0xFF, 0x49)


def test_hex_to_rgb_without_hash() -> None:
    assert hex_to_rgb("43FF49") == (0x43, 0xFF, 0x49)


def test_hex_to_rgb_rejects_bad_input() -> None:
    assert hex_to_rgb("#ABC") is None
    assert hex_to_rgb("#GGGGGG") is None
    assert hex_to_rgb("") is None


def test_rgb_to_hex_round_trip() -> None:
    assert rgb_to_hex((0x43, 0xFF, 0x49)) == "#43FF49"
    assert rgb_to_hex((0, 0, 0)) == "#000000"
    assert rgb_to_hex((255, 255, 255)) == "#FFFFFF"


def test_rgb_to_hex_clamps_out_of_range() -> None:
    assert rgb_to_hex((-10, 300, 128)) == "#00FF80"


def test_raw_feature_value_finds_match() -> None:
    data = {
        "featureItems": [
            {"itemKey": "light_mode", "dataValue": "white"},
            {"itemKey": "color_rgb", "dataValue": "#43FF49"},
        ]
    }
    assert raw_feature_value(data, "light_mode") == "white"
    assert raw_feature_value(data, "color_rgb") == "#43FF49"


def test_raw_feature_value_missing_key_or_items() -> None:
    assert raw_feature_value({"featureItems": []}, "light_mode") is None
    assert raw_feature_value({}, "light_mode") is None
