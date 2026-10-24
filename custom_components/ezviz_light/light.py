"""Light platform for the EZVIZ Light integration."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from pyezvizapi.exceptions import PyEzvizError

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_RGB_COLOR,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import state
from .brightness import EZVIZ_BRIGHTNESS_MAX, ezviz_to_ha_brightness, ha_to_ezviz_brightness
from .color import raw_feature_value, rgb_to_hex
from .const import (
    DEVICE_STATUS_OFFLINE,
    DOMAIN,
    FEATURE_KEY_BRIGHTNESS,
    FEATURE_KEY_COLOR_RGB,
    FEATURE_KEY_COLOR_TEMPERATURE,
    FEATURE_KEY_LIGHT_MODE,
    FEATURE_KEY_LIGHT_SWITCH,
    MANUFACTURER,
)
from .coordinator import EzvizLightConfigEntry, EzvizLightCoordinator

_LOGGER = logging.getLogger(__name__)

# Backoff (seconds) before each retry in _run_with_retry, after the first
# (immediate) attempt - e.g. (1, 2) means try, wait 1s, try, wait 2s, try.
WRITE_RETRY_DELAYS_SECONDS = (1, 2)

# How long to let a burst of commands accumulate before sending the next
# batch. Only applies once a write is already in flight, so a single tap
# still goes out immediately; a slider drag collapses into one more write
# of its final value instead of one write per intermediate step.
WRITE_DEBOUNCE_SECONDS = 0.3

# state.py's color modes are plain strings (so that module stays
# homeassistant-free and testable against examples/*.json); map them to
# HA's ColorMode here, at the HA-facing edge.
_STATE_TO_HA_COLOR_MODE = {
    state.COLOR_MODE_BRIGHTNESS: ColorMode.BRIGHTNESS,
    state.COLOR_MODE_COLOR_TEMP: ColorMode.COLOR_TEMP,
    state.COLOR_MODE_RGB: ColorMode.RGB,
}

# Order writes are sent in: power first, then the value being set, then
# the mode switch last so it applies to the value we just wrote.
_WRITE_ORDER = (
    FEATURE_KEY_LIGHT_SWITCH,
    FEATURE_KEY_BRIGHTNESS,
    FEATURE_KEY_COLOR_TEMPERATURE,
    FEATURE_KEY_COLOR_RGB,
    FEATURE_KEY_LIGHT_MODE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: EzvizLightConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EZVIZ light bulbs from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        EzvizLightBulbEntity(coordinator, serial) for serial in coordinator.data
    )


class EzvizLightBulbEntity(CoordinatorEntity[EzvizLightCoordinator], LightEntity):
    """Representation of an EZVIZ light bulb (e.g. LB1)."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(self, coordinator: EzvizLightCoordinator, serial: str) -> None:
        """Initialize the light entity for one bulb serial."""
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = serial
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, serial)},
            manufacturer=MANUFACTURER,
            model=self._data.get("device_sub_category"),
            name=self._data.get("name") or serial,
        )

        # Feature writes waiting to be sent, coalesced by key (latest wins),
        # drained by a single background task so writes can't interleave.
        self._pending: dict[str, Any] = {}
        self._write_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

        supported = state.supported_color_mode_names(self._data)
        self._attr_supported_color_modes = {
            _STATE_TO_HA_COLOR_MODE[mode] for mode in supported
        }
        if state.COLOR_MODE_COLOR_TEMP in supported:
            # Prefers the device's own reported range (confirmed present on
            # a live LB1-Color) over the LB1-Color spec sheet's range.
            min_kelvin, max_kelvin = state.color_temp_kelvin_range(self._data)
            self._attr_min_color_temp_kelvin = min_kelvin
            self._attr_max_color_temp_kelvin = max_kelvin

    @property
    def _data(self) -> dict[str, Any]:
        return self.coordinator.data[self._serial]

    @property
    def _supported_modes(self) -> set[str]:
        return {
            mode
            for mode, ha_mode in _STATE_TO_HA_COLOR_MODE.items()
            if ha_mode in self._attr_supported_color_modes
        }

    @property
    def available(self) -> bool:
        """Unavailable if the poll failed, the serial vanished, or EZVIZ says it's offline.

        The offline check matters: while a bulb is unreachable the cloud
        keeps serving its last known state, so without this the entity
        shows a confident on/off value that may be wrong and that no UI
        action can correct - the toggle derives its intent from that same
        stale value, so it keeps sending the wrong command. Missing status
        is treated as online, since not every payload carries the field.
        """
        return (
            super().available
            and self._serial in self.coordinator.data
            and self._data.get("status") != DEVICE_STATUS_OFFLINE
        )

    @property
    def is_on(self) -> bool:
        """Return whether the bulb is on."""
        return state.is_on(self._data)

    @property
    def brightness(self) -> int:
        """Return brightness scaled to HA's 0-255 range."""
        return ezviz_to_ha_brightness(self._data.get("brightness", EZVIZ_BRIGHTNESS_MAX))

    @property
    def color_mode(self) -> ColorMode:
        """Return the currently active color mode, from the device's own light_mode."""
        return _STATE_TO_HA_COLOR_MODE[
            state.current_color_mode_name(self._data, self._supported_modes)
        ]

    @property
    def color_temp_kelvin(self) -> int | None:
        """Return color temperature; see state.color_temp_kelvin for caveats."""
        return state.color_temp_kelvin(
            self._data, state.current_color_mode_name(self._data, self._supported_modes)
        )

    @property
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Return the current RGB color, parsed from the "#RRGGBB" feature value."""
        return state.rgb_color(
            self._data, state.current_color_mode_name(self._data, self._supported_modes)
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            # Diagnostics for on/off desync: what the cloud claims the
            # switch is, and whether it considers the bulb reachable.
            "ezviz_light_switch_raw": raw_feature_value(
                self._data, FEATURE_KEY_LIGHT_SWITCH
            ),
            "ezviz_device_status_raw": self._data.get("status"),
            "ezviz_light_mode_raw": raw_feature_value(self._data, FEATURE_KEY_LIGHT_MODE),
            "ezviz_color_temperature_raw": self._data.get("color_temperature"),
            "ezviz_color_rgb_raw": raw_feature_value(self._data, FEATURE_KEY_COLOR_RGB),
            # Full raw feature list, kept for spotting any other item keys
            # (scene/music payloads, etc.) that might be worth wiring up later.
            "ezviz_feature_items_raw": self._data.get("featureItems"),
        }

    def _write_feature(self, product_id: Any, value: Any, item_key: str) -> None:
        # Calls the client directly rather than going through
        # pyezvizapi.EzvizLightBulb - constructing one does a full
        # get_device_infos() (every device on the account, not just this
        # bulb) purely to read back the same product_id we already have
        # cached from the last poll. Skipping it is what cut the ~3s lag
        # per write down to roughly one network round trip.
        self.coordinator.client.set_device_feature_by_key(
            self._serial, product_id, value, item_key
        )

    def _run_with_retry(self, action: Callable[[], None]) -> None:
        """Run a blocking pyezvizapi write, retrying after a transient failure.

        EZVIZ's cloud occasionally times out waiting for the physical bulb
        to ack a push (meta code 2009, "设备请求响应超时" / device request
        response timeout) - seen live, repeatedly, on color_rgb writes. A
        single retry wasn't always enough, so this now tries up to
        WRITE_RETRY_DELAYS_SECONDS additional times with increasing
        backoff between attempts. Every write this integration makes
        (power on/off, brightness, color_rgb, color_temperature,
        light_mode) is idempotent, so retrying the whole closure is safe.
        Without this, PyEzvizError propagated all the way to HA's
        websocket layer as a raw, unfriendly traceback.
        """
        last_error: PyEzvizError | None = None
        for attempt, delay in enumerate((0, *WRITE_RETRY_DELAYS_SECONDS)):
            if delay:
                time.sleep(delay)
            try:
                action()
                return
            except PyEzvizError as err:
                last_error = err
                _LOGGER.debug(
                    "EZVIZ command failed on attempt %d (%s)", attempt + 1, err
                )
        raise HomeAssistantError(f"EZVIZ didn't accept the command: {last_error}") from last_error

    def _apply_optimistic(self, writes: dict[str, Any]) -> None:
        """Show queued writes in the UI straight away, before they're sent.

        Patches the status dict in place - top-level fields for
        switch/brightness/color temp, the matching featureItems entry for
        color_rgb/light_mode (which is where state.py reads those from).

        Nothing re-verifies the write afterwards. An earlier version
        polled a few seconds later to "confirm" it, which turned out to
        be the cause of visible flicker: EZVIZ's write API acks once the
        cloud has *accepted* a command, before the device has necessarily
        reported its new status back, so that confirming read often saw
        stale pre-write data and snapped the UI back - confirmed live
        (brightness appeared to never change). Failed writes are logged
        and followed by a refresh in _flush_pending instead, and the
        regular 30s poll still catches any silent mismatch.
        """
        data = self._data
        for key, value in writes.items():
            field = state.FEATURE_TO_STATUS_FIELD.get(key)
            if field is not None:
                data[field] = value
            else:
                for item in data.get("featureItems") or []:
                    if item.get("itemKey") == key:
                        item["dataValue"] = value
        self.async_write_ha_state()

    def _queue_writes(self, writes: dict[str, Any]) -> None:
        """Show writes immediately, then send them in the background.

        Returning without awaiting the cloud is what makes the UI feel
        instant; coalescing into _pending is what stops a slider drag
        from queueing a separate round trip per intermediate value.
        """
        if not writes:
            return
        self._pending.update(writes)
        self._apply_optimistic(writes)
        if self._write_task is None or self._write_task.done():
            self._write_task = self.hass.async_create_task(self._flush_pending())

    async def _flush_pending(self) -> None:
        """Drain _pending one batch at a time until nothing is left."""
        async with self._write_lock:
            while self._pending:
                writes = self._pending
                self._pending = {}
                try:
                    await self.hass.async_add_executor_job(self._write_batch, writes)
                except HomeAssistantError as err:
                    # Outside a service call there's nobody to raise to, so
                    # surface it in the log and refresh so the UI drops the
                    # optimistic value and shows what the bulb actually did.
                    _LOGGER.error("EZVIZ write failed for %s: %s", self.entity_id, err)
                    self._pending.clear()
                    await self.coordinator.async_request_refresh()
                    return
                if self._pending:
                    await asyncio.sleep(WRITE_DEBOUNCE_SECONDS)

    def _write_batch(self, writes: dict[str, Any]) -> None:
        """Send one coalesced batch, in _WRITE_ORDER, from the executor."""
        product_id = self._data.get("productId")
        for key in _WRITE_ORDER:
            if key not in writes:
                continue
            value = writes[key]
            self._run_with_retry(
                lambda v=value, k=key: self._write_feature(product_id, v, k)
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the bulb on, optionally setting brightness, color temp, or RGB."""
        use_rgb = (
            ATTR_RGB_COLOR in kwargs
            and ColorMode.RGB in self._attr_supported_color_modes
        )
        use_color_temp = (
            ATTR_COLOR_TEMP_KELVIN in kwargs
            and ColorMode.COLOR_TEMP in self._attr_supported_color_modes
        )
        self._queue_writes(
            state.plan_writes(
                self._data,
                brightness=(
                    ha_to_ezviz_brightness(kwargs[ATTR_BRIGHTNESS])
                    if ATTR_BRIGHTNESS in kwargs
                    else None
                ),
                color_rgb=rgb_to_hex(kwargs[ATTR_RGB_COLOR]) if use_rgb else None,
                color_temp_kelvin=(
                    kwargs[ATTR_COLOR_TEMP_KELVIN] if use_color_temp else None
                ),
                turn_on=True,
            )
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the bulb off."""
        self._queue_writes({FEATURE_KEY_LIGHT_SWITCH: False})

    async def async_toggle(self, **kwargs: Any) -> None:
        """Toggle based on the last-known state (fast path - see README caveat).

        Previously re-fetched the device's live state first to toggle
        correctly even if it was flipped from the EZVIZ app between polls;
        now uses self._data (up to ~30s stale) instead, trading that
        guarantee for cutting out a full extra cloud round trip per toggle.
        """
        self._queue_writes({FEATURE_KEY_LIGHT_SWITCH: not self.is_on})

    async def async_will_remove_from_hass(self) -> None:
        """Drop any queued writes when the entity goes away."""
        self._pending.clear()
        if self._write_task is not None and not self._write_task.done():
            self._write_task.cancel()
        await super().async_will_remove_from_hass()
