"""Unit tests for EZVIZ <-> HA brightness scaling.

brightness.py has zero imports (not even from the rest of the integration),
so it's loaded directly by file path here rather than via
``custom_components.ezviz_light.brightness`` - that dotted import would
first run the package's ``__init__.py``, which imports homeassistant/
pyezvizapi. This keeps the test runnable with neither installed.
"""

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ezviz_light_brightness",
    Path(__file__).parent.parent / "custom_components" / "ezviz_light" / "brightness.py",
)
assert _SPEC is not None and _SPEC.loader is not None
brightness = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(brightness)

ezviz_to_ha_brightness = brightness.ezviz_to_ha_brightness
ha_to_ezviz_brightness = brightness.ha_to_ezviz_brightness


def test_ezviz_to_ha_boundaries() -> None:
    assert ezviz_to_ha_brightness(1) == 3
    assert ezviz_to_ha_brightness(50) == 128
    assert ezviz_to_ha_brightness(100) == 255


def test_ezviz_to_ha_clamps_out_of_range() -> None:
    assert ezviz_to_ha_brightness(0) == ezviz_to_ha_brightness(1)
    assert ezviz_to_ha_brightness(150) == ezviz_to_ha_brightness(100)


def test_ha_to_ezviz_boundaries() -> None:
    assert ha_to_ezviz_brightness(1) == 1
    assert ha_to_ezviz_brightness(128) == 50
    assert ha_to_ezviz_brightness(255) == 100


def test_ha_to_ezviz_clamps_out_of_range() -> None:
    assert ha_to_ezviz_brightness(0) == ha_to_ezviz_brightness(1)
    assert ha_to_ezviz_brightness(300) == ha_to_ezviz_brightness(255)


def test_round_trip_is_close() -> None:
    for value in (1, 25, 50, 75, 100):
        ha_value = ezviz_to_ha_brightness(value)
        assert abs(ha_to_ezviz_brightness(ha_value) - value) <= 1
