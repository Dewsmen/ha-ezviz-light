"""Brightness scaling between EZVIZ's 1-100 range and HA's 0-255 range.

Standalone (no homeassistant/pyezvizapi imports) so it can be unit tested
without those dependencies installed.
"""

EZVIZ_BRIGHTNESS_MIN = 1
EZVIZ_BRIGHTNESS_MAX = 100


def ezviz_to_ha_brightness(value: int) -> int:
    """Scale EZVIZ's 1-100 brightness to HA's 1-255."""
    value = max(EZVIZ_BRIGHTNESS_MIN, min(EZVIZ_BRIGHTNESS_MAX, value))
    return round(value / EZVIZ_BRIGHTNESS_MAX * 255)


def ha_to_ezviz_brightness(value: int) -> int:
    """Scale HA's 1-255 brightness to EZVIZ's 1-100."""
    value = max(1, min(255, value))
    return max(EZVIZ_BRIGHTNESS_MIN, round(value / 255 * EZVIZ_BRIGHTNESS_MAX))
