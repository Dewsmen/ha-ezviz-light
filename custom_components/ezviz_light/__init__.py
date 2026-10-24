"""The EZVIZ Light integration."""

from __future__ import annotations

from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import (
    EzvizAuthTokenExpired,
    EzvizAuthVerificationCode,
    HTTPError,
    InvalidURL,
    PyEzvizError,
)

from homeassistant.const import CONF_URL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .const import CONF_RFSESSION_ID, CONF_SESSION_ID, DEFAULT_TIMEOUT
from .coordinator import EzvizLightConfigEntry, EzvizLightCoordinator

PLATFORMS: list[Platform] = [Platform.LIGHT]


async def async_setup_entry(hass: HomeAssistant, entry: EzvizLightConfigEntry) -> bool:
    """Set up EZVIZ Light from a config entry."""
    client = EzvizClient(
        token={
            CONF_SESSION_ID: entry.data[CONF_SESSION_ID],
            CONF_RFSESSION_ID: entry.data[CONF_RFSESSION_ID],
            "api_url": entry.data[CONF_URL],
        },
        timeout=DEFAULT_TIMEOUT,
    )

    try:
        await hass.async_add_executor_job(client.login)
    except (EzvizAuthTokenExpired, EzvizAuthVerificationCode) as err:
        raise ConfigEntryAuthFailed from err
    except (InvalidURL, HTTPError, PyEzvizError) as err:
        raise ConfigEntryNotReady(f"Unable to connect to EZVIZ: {err}") from err

    coordinator = EzvizLightCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: EzvizLightConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
