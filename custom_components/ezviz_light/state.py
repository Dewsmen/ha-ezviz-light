"""Pure derivation of light state from an EZVIZ bulb status dict.

Standalone (only depends on the equally-standalone .color and .const), so
it's testable directly against examples/*.json fixtures without
homeassistant/pyezvizapi installed. light.py is a thin adapter from these
functions onto HA's LightEntity/ColorMode API.

Color modes are passed around as plain strings here ("brightness",
"color_temp", "rgb") rather than homeassistant.components.light.ColorMode,
precisely so this module doesn't need homeassistant installed to test.
"""

from __future__ import annotations

from typing import Any

from .color import hex_to_rgb, parse_data_desc, raw_feature_item, raw_feature_value
from .const import (
    EZVIZ_MAX_COLOR_TEMP_KELVIN,
    EZVIZ_MIN_COLOR_TEMP_KELVIN,
    FEATURE_KEY_BRIGHTNESS,
    FEATURE_KEY_COLOR_RGB,
    FEATURE_KEY_COLOR_TEMPERATURE,
    FEATURE_KEY_LIGHT_MODE,
    FEATURE_KEY_LIGHT_SWITCH,
    LIGHT_MODE_COLOR,
    LIGHT_MODE_WHITE,
)

COLOR_MODE_BRIGHTNESS = "brightness"
COLOR_MODE_COLOR_TEMP = "color_temp"
COLOR_MODE_RGB = "rgb"

# Where each feature key's value lives in the status dict. Keys listed
# here map to a top-level field; anything else (color_rgb, light_mode)
# lives inside the featureItems list.
FEATURE_TO_STATUS_FIELD = {
    FEATURE_KEY_LIGHT_SWITCH: "is_on",
    FEATURE_KEY_BRIGHTNESS: "brightness",
    FEATURE_KEY_COLOR_TEMPERATURE: "color_temperature",
}


def supported_color_mode_names(data: dict[str, Any]) -> set[str]:
    """Which color modes this bulb supports, from its first-seen feature set.

    LB1-White has neither color_temperature nor color_rgb; LB1-Color has
    both. Detected from the device's own reported features, not hardcoded
    by model name, since both variants report the same device_sub_category.
    """
    modes: set[str] = set()
    if data.get("color_temperature") is not None:
        modes.add(COLOR_MODE_COLOR_TEMP)
    if raw_feature_value(data, FEATURE_KEY_COLOR_RGB) is not None:
        modes.add(COLOR_MODE_RGB)
    return modes or {COLOR_MODE_BRIGHTNESS}


def current_color_mode_name(data: dict[str, Any], supported: set[str]) -> str:
    """Which color mode is active right now, from the device's own light_mode.

    light_mode is confirmed rw: "white" selects color_temperature, "color"
    selects color_rgb (also "scene"/"music", which this integration doesn't
    control - falls back to whichever of the two above this bulb supports).
    """
    if supported == {COLOR_MODE_BRIGHTNESS}:
        return COLOR_MODE_BRIGHTNESS
    if (
        raw_feature_value(data, FEATURE_KEY_LIGHT_MODE) == LIGHT_MODE_COLOR
        and COLOR_MODE_RGB in supported
    ):
        return COLOR_MODE_RGB
    if COLOR_MODE_COLOR_TEMP in supported:
        return COLOR_MODE_COLOR_TEMP
    return next(iter(supported))


def is_on(data: dict[str, Any]) -> bool:
    """Return whether the bulb is on."""
    return bool(data.get("is_on"))


def _color_temp_kelvin_params(data: dict[str, Any]) -> tuple[int, int, int]:
    """Return (min, max, interval) Kelvin, preferring the device's own values.

    Confirmed on a live LB1-Color: color_temperature's dataDesc is
    '{"range_from": 2700, "range_to": 6500, "interval": 10, "unit": "k"}' -
    the unit (Kelvin), the range, AND the step all come straight from the
    device. The interval is enforced server-side: writing a value not on
    that grid is rejected outright ("num not meets interval", code 400),
    confirmed live. Falls back to the LB1-Color spec sheet's range (and an
    interval of 1, i.e. no snapping) if dataDesc is missing or malformed.
    """
    item = raw_feature_item(data, FEATURE_KEY_COLOR_TEMPERATURE)
    desc = parse_data_desc(item.get("dataDesc")) if item else {}
    low, high, interval = desc.get("range_from"), desc.get("range_to"), desc.get("interval")
    if isinstance(low, int) and isinstance(high, int) and low < high:
        if not isinstance(interval, int) or interval <= 0:
            interval = 1
        return low, high, interval
    return EZVIZ_MIN_COLOR_TEMP_KELVIN, EZVIZ_MAX_COLOR_TEMP_KELVIN, 1


def color_temp_kelvin_range(data: dict[str, Any]) -> tuple[int, int]:
    """Return (min, max) Kelvin - see _color_temp_kelvin_params for sourcing."""
    low, high, _interval = _color_temp_kelvin_params(data)
    return low, high


def snap_color_temp_kelvin(data: dict[str, Any], value: int) -> int:
    """Clamp a requested Kelvin value into range AND snap it to the device's step.

    HA's color-temp slider/service can request any integer Kelvin in
    range; the device only accepts values on its own range_from + k *
    interval grid (confirmed live: 6334 was rejected, interval is 10).
    """
    low, high, interval = _color_temp_kelvin_params(data)
    clamped = max(low, min(high, value))
    snapped = low + round((clamped - low) / interval) * interval
    return max(low, min(high, snapped))


def color_temp_kelvin(data: dict[str, Any], color_mode: str) -> int | None:
    """Return color temperature in Kelvin (confirmed unit - see README).

    Only returned while color_mode is COLOR_MODE_COLOR_TEMP.
    """
    if color_mode != COLOR_MODE_COLOR_TEMP:
        return None
    raw = data.get("color_temperature")
    if raw is None:
        return None
    low, high = color_temp_kelvin_range(data)
    return max(low, min(high, int(raw)))


def current_feature_value(data: dict[str, Any], item_key: str) -> Any:
    """Return the currently known value for a feature key."""
    field = FEATURE_TO_STATUS_FIELD.get(item_key)
    if field is not None:
        return data.get(field)
    return raw_feature_value(data, item_key)


def plan_writes(
    data: dict[str, Any],
    *,
    brightness: int | None = None,
    color_rgb: str | None = None,
    color_temp_kelvin: int | None = None,
    turn_on: bool = False,
) -> dict[str, Any]:
    """Work out the minimal set of feature writes for a turn-on request.

    Values come in already in device units (brightness 1-100, color_rgb
    as "#RRGGBB"); color_temp_kelvin is snapped here. Every write costs a
    cloud round trip, so anything already at the requested value is
    dropped - which mostly saves the light_mode write when adjusting a
    color the bulb is already rendering in that mode.

    The power write is only added when it's actually needed: the bulb
    isn't on, or there's nothing else to send (in which case honour the
    explicit intent, since our cached state may be stale).
    """
    writes: dict[str, Any] = {}
    if brightness is not None:
        writes[FEATURE_KEY_BRIGHTNESS] = brightness
    if color_rgb is not None:
        # Setting color_rgb alone doesn't necessarily make it the one
        # rendered - the device has to be in color mode as well.
        writes[FEATURE_KEY_COLOR_RGB] = color_rgb
        writes[FEATURE_KEY_LIGHT_MODE] = LIGHT_MODE_COLOR
    elif color_temp_kelvin is not None:
        writes[FEATURE_KEY_COLOR_TEMPERATURE] = snap_color_temp_kelvin(
            data, color_temp_kelvin
        )
        writes[FEATURE_KEY_LIGHT_MODE] = LIGHT_MODE_WHITE

    writes = {
        key: value
        for key, value in writes.items()
        if current_feature_value(data, key) != value
    }

    if turn_on and (not is_on(data) or not writes):
        writes[FEATURE_KEY_LIGHT_SWITCH] = True
    return writes


def rgb_color(data: dict[str, Any], color_mode: str) -> tuple[int, int, int] | None:
    """Return the current RGB color, parsed from the "#RRGGBB" feature value.

    Only returned while color_mode is COLOR_MODE_RGB.
    """
    if color_mode != COLOR_MODE_RGB:
        return None
    raw = raw_feature_value(data, FEATURE_KEY_COLOR_RGB)
    return hex_to_rgb(raw) if isinstance(raw, str) else None
