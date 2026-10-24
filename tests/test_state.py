"""Unit tests for state.py against examples/real_lb1_color_capture.json.

That fixture's featureItems are copied verbatim from a real LB1-Color
bulb (see examples/README.md) - not a reconstruction.

state.py has relative imports to .color/.const (both equally dependency-
free), so unlike test_brightness.py/test_color.py a plain
spec_from_file_location load isn't enough - Python needs those relative
imports to resolve against a real parent package. This registers minimal
namespace-package stand-ins in sys.modules for "custom_components" and
"custom_components.ezviz_light" (just enough for submodule resolution),
so .color/.const load for real without ever running __init__.py (which
needs homeassistant/pyezvizapi installed).
"""

import copy
import importlib.util
import json
import sys
import types
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_PACKAGE_DIR = _PROJECT_ROOT / "custom_components" / "ezviz_light"


def _ensure_namespace_package(dotted_name: str, path: Path) -> None:
    if dotted_name in sys.modules:
        return
    module = types.ModuleType(dotted_name)
    module.__path__ = [str(path)]  # type: ignore[attr-defined]
    sys.modules[dotted_name] = module


_ensure_namespace_package("custom_components", _PACKAGE_DIR.parent)
_ensure_namespace_package("custom_components.ezviz_light", _PACKAGE_DIR)

_spec = importlib.util.spec_from_file_location(
    "custom_components.ezviz_light.state", _PACKAGE_DIR / "state.py"
)
assert _spec is not None and _spec.loader is not None
state = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = state
_spec.loader.exec_module(state)

def _load_fixture(name: str) -> dict:
    return json.loads((_PROJECT_ROOT / "examples" / name).read_text())


REAL_CAPTURE = _load_fixture("real_lb1_color_capture.json")
REAL_WHITE_CAPTURE = _load_fixture("real_white_mode_capture.json")
REAL_RGB_CAPTURE = _load_fixture("real_rgb_mode_capture.json")
REAL_SCENE_CAPTURE = _load_fixture("real_scene_mode_capture.json")


def _with_light_mode(data: dict, mode: str) -> dict:
    data = copy.deepcopy(data)
    for item in data["featureItems"]:
        if item["itemKey"] == "light_mode":
            item["dataValue"] = mode
    return data


def test_real_capture_supports_both_color_modes() -> None:
    assert state.supported_color_mode_names(REAL_CAPTURE) == {
        state.COLOR_MODE_COLOR_TEMP,
        state.COLOR_MODE_RGB,
    }


def test_white_only_bulb_detection() -> None:
    # Synthetic, since no real LB1-White capture exists yet (see
    # examples/README.md) - LB1-White has neither color_temperature nor
    # color_rgb at all, unlike LB1-Color.
    white_only = {
        "is_on": True,
        "brightness": 50,
        "featureItems": [
            {"itemKey": "light_switch", "dataValue": True},
            {"itemKey": "brightness", "dataValue": 50},
        ],
    }
    assert state.supported_color_mode_names(white_only) == {state.COLOR_MODE_BRIGHTNESS}
    assert (
        state.current_color_mode_name(white_only, {state.COLOR_MODE_BRIGHTNESS})
        == state.COLOR_MODE_BRIGHTNESS
    )


def test_current_mode_matches_real_capture_light_mode_color() -> None:
    # The real capture's raw light_mode is "color" - confirm our detection
    # picks that up (the HA entity attributes captured alongside it showed
    # color_mode: color_temp, but that predates this light_mode-aware
    # property; this fixture is the regression test for it).
    supported = state.supported_color_mode_names(REAL_CAPTURE)
    assert state.current_color_mode_name(REAL_CAPTURE, supported) == state.COLOR_MODE_RGB


def test_real_white_mode_capture_end_to_end() -> None:
    # light_mode: white, and the HA attributes captured alongside this one
    # agree (color_mode: color_temp, color_temp_kelvin: 6500) - the first
    # fully self-consistent capture (earlier ones predated this logic).
    supported = state.supported_color_mode_names(REAL_WHITE_CAPTURE)
    mode = state.current_color_mode_name(REAL_WHITE_CAPTURE, supported)
    assert mode == state.COLOR_MODE_COLOR_TEMP
    assert state.color_temp_kelvin(REAL_WHITE_CAPTURE, mode) == 6500
    assert state.rgb_color(REAL_WHITE_CAPTURE, mode) is None


def test_real_rgb_mode_capture_end_to_end() -> None:
    # light_mode: color, and the HA attributes captured alongside this one
    # agree (color_mode: rgb, rgb_color: [255, 72, 1] from "#FF4801") -
    # confirms RGB works end to end on a live device, not just in theory.
    supported = state.supported_color_mode_names(REAL_RGB_CAPTURE)
    mode = state.current_color_mode_name(REAL_RGB_CAPTURE, supported)
    assert mode == state.COLOR_MODE_RGB
    assert state.rgb_color(REAL_RGB_CAPTURE, mode) == (255, 72, 1)
    assert state.color_temp_kelvin(REAL_RGB_CAPTURE, mode) is None


def test_real_scene_mode_capture_falls_back_to_color_temp() -> None:
    # light_mode: scene. The HA attributes captured alongside this one
    # also showed color_mode: color_temp (not reflecting the scene's
    # actual cycling colors #FF4D00/#FFFF00) - confirms live that scene
    # playback is genuinely invisible to this integration, as documented.
    supported = state.supported_color_mode_names(REAL_SCENE_CAPTURE)
    mode = state.current_color_mode_name(REAL_SCENE_CAPTURE, supported)
    assert mode == state.COLOR_MODE_COLOR_TEMP
    assert state.color_temp_kelvin(REAL_SCENE_CAPTURE, mode) == 6500


def test_current_mode_falls_back_for_music_too() -> None:
    # No real "music" capture exists; music should fall back identically
    # to scene (no HA ColorMode matches either).
    data = _with_light_mode(REAL_SCENE_CAPTURE, "music")
    supported = state.supported_color_mode_names(data)
    assert state.current_color_mode_name(data, supported) == state.COLOR_MODE_COLOR_TEMP


def test_plan_writes_skips_power_when_already_on() -> None:
    # Adjusting an already-on bulb shouldn't spend a round trip re-sending
    # light_switch=True - that was doubling the latency of every drag.
    writes = state.plan_writes(REAL_RGB_CAPTURE, brightness=42, turn_on=True)
    assert writes == {"brightness": 42}


def test_plan_writes_includes_power_when_off() -> None:
    data = copy.deepcopy(REAL_RGB_CAPTURE)
    data["is_on"] = False
    writes = state.plan_writes(data, brightness=42, turn_on=True)
    assert writes == {"brightness": 42, "light_switch": True}


def test_plan_writes_bare_turn_on_still_sends_power() -> None:
    # Nothing else to send: honour the explicit intent even though we
    # believe it's already on, since that belief may be stale.
    assert state.plan_writes(REAL_RGB_CAPTURE, turn_on=True) == {"light_switch": True}


def test_plan_writes_drops_values_already_set() -> None:
    # The real RGB capture is already #FF4801 in color mode, so asking for
    # exactly that should produce no writes at all beyond the power rule.
    writes = state.plan_writes(REAL_RGB_CAPTURE, color_rgb="#FF4801", turn_on=False)
    assert writes == {}


def test_plan_writes_skips_redundant_mode_switch() -> None:
    # Already in color mode: changing colour shouldn't also re-send
    # light_mode, saving a second round trip.
    writes = state.plan_writes(REAL_RGB_CAPTURE, color_rgb="#00FF00", turn_on=False)
    assert writes == {"color_rgb": "#00FF00"}


def test_plan_writes_includes_mode_switch_when_changing_mode() -> None:
    # The white-mode capture is in light_mode "white", so setting a colour
    # must flip the mode as well as the value.
    writes = state.plan_writes(REAL_WHITE_CAPTURE, color_rgb="#00FF00", turn_on=False)
    assert writes == {"color_rgb": "#00FF00", "light_mode": "color"}


def test_plan_writes_color_temp_snaps_and_sets_white_mode() -> None:
    writes = state.plan_writes(REAL_RGB_CAPTURE, color_temp_kelvin=6334, turn_on=False)
    assert writes == {"color_temperature": 6330, "light_mode": "white"}


def test_plan_writes_rgb_takes_precedence_over_color_temp() -> None:
    writes = state.plan_writes(
        REAL_WHITE_CAPTURE, color_rgb="#00FF00", color_temp_kelvin=3000, turn_on=False
    )
    assert "color_temperature" not in writes
    assert writes["color_rgb"] == "#00FF00"


def test_snap_color_temp_kelvin_rejects_regression_value() -> None:
    # 6334 is the exact value HA's slider sent that the real device
    # rejected live: "num not meets interval : 6334" (interval is 10,
    # confirmed via dataDesc). Must now land on the 10K grid.
    snapped = state.snap_color_temp_kelvin(REAL_CAPTURE, 6334)
    assert snapped % 10 == 0
    assert snapped == 6330


def test_snap_color_temp_kelvin_exact_multiple_unchanged() -> None:
    assert state.snap_color_temp_kelvin(REAL_CAPTURE, 4000) == 4000


def test_snap_color_temp_kelvin_clamps_out_of_range_before_snapping() -> None:
    assert state.snap_color_temp_kelvin(REAL_CAPTURE, 100) == 2700
    assert state.snap_color_temp_kelvin(REAL_CAPTURE, 99999) == 6500


def test_snap_color_temp_kelvin_no_snapping_when_interval_missing() -> None:
    data = copy.deepcopy(REAL_CAPTURE)
    for item in data["featureItems"]:
        if item["itemKey"] == "color_temperature":
            item["dataDesc"] = "not valid json"
    # Falls back to EZVIZ_MIN/MAX with interval 1 - any in-range int passes through.
    assert state.snap_color_temp_kelvin(data, 6334) == 6334


def test_color_temp_kelvin_range_from_real_device_data_desc() -> None:
    # Confirmed real range/unit: dataDesc on the real capture's
    # color_temperature item is '{"range_from": 2700, "range_to": 6500,
    # "interval": 10, "unit": "k"}'.
    assert state.color_temp_kelvin_range(REAL_CAPTURE) == (2700, 6500)


def test_color_temp_kelvin_range_falls_back_when_data_desc_missing() -> None:
    data = copy.deepcopy(REAL_CAPTURE)
    for item in data["featureItems"]:
        if item["itemKey"] == "color_temperature":
            item["dataDesc"] = "not valid json"
    assert state.color_temp_kelvin_range(data) == (
        state.EZVIZ_MIN_COLOR_TEMP_KELVIN,
        state.EZVIZ_MAX_COLOR_TEMP_KELVIN,
    )


def test_color_temp_kelvin_only_in_color_temp_mode() -> None:
    assert state.color_temp_kelvin(REAL_CAPTURE, state.COLOR_MODE_RGB) is None
    assert state.color_temp_kelvin(REAL_CAPTURE, state.COLOR_MODE_COLOR_TEMP) == 6500


def test_rgb_color_only_in_rgb_mode() -> None:
    assert state.rgb_color(REAL_CAPTURE, state.COLOR_MODE_COLOR_TEMP) is None
    assert state.rgb_color(REAL_CAPTURE, state.COLOR_MODE_RGB) == (0x43, 0xFF, 0x49)


def test_is_on() -> None:
    assert state.is_on(REAL_CAPTURE) is True
    assert state.is_on({"is_on": False}) is False
    assert state.is_on({}) is False
