"""DataUpdateCoordinator for the EZVIZ Light integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import (
    EzvizAuthTokenExpired,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_TIMEOUT, DOMAIN, UPDATE_INTERVAL_SECONDS
from .state import copy_status

_LOGGER = logging.getLogger(__name__)

EzvizLightConfigEntry = ConfigEntry["EzvizLightCoordinator"]


class EzvizLightCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that polls EZVIZ cloud for light bulb status."""

    def __init__(
        self, hass: HomeAssistant, entry: EzvizLightConfigEntry, client: EzvizClient
    ) -> None:
        """Initialize the coordinator."""
        self.client = client
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest light bulb status map from the EZVIZ cloud."""
        try:
            async with asyncio.timeout(DEFAULT_TIMEOUT):
                bulbs = await self.hass.async_add_executor_job(
                    self.client.load_light_bulbs, True
                )
        except (EzvizAuthTokenExpired, EzvizAuthVerificationCode) as err:
            raise ConfigEntryAuthFailed from err
        except (InvalidURL, HTTPError, PyEzvizError) as err:
            raise UpdateFailed(f"Error communicating with EZVIZ: {err}") from err

        return {serial: copy_status(status) for serial, status in bulbs.items()}
