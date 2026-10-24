"""Config flow for the EZVIZ Light integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from pyezvizapi.client import EzvizClient
from pyezvizapi.exceptions import (
    EzvizAuthVerificationCode,
    InvalidHost,
    InvalidURL,
    PyEzvizError,
)

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_URL, CONF_USERNAME

from .const import (
    CONF_RFSESSION_ID,
    CONF_SESSION_ID,
    DEFAULT_TIMEOUT,
    DOMAIN,
    EU_URL,
    OPTION_CUSTOM_URL,
    RUSSIA_URL,
)

_LOGGER = logging.getLogger(__name__)


def _login(username: str, password: str, url: str, sms_code: int | None) -> dict[str, Any]:
    """Log in to the EZVIZ cloud and return the token data to persist.

    Runs in an executor job; this performs blocking network I/O.
    """
    client = EzvizClient(username, password, url, DEFAULT_TIMEOUT)
    token = client.login(sms_code=sms_code)
    return {
        CONF_USERNAME: username,
        CONF_SESSION_ID: token[CONF_SESSION_ID],
        CONF_RFSESSION_ID: token[CONF_RFSESSION_ID],
        CONF_URL: token["api_url"],
    }


class EzvizLightConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EZVIZ Light."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow's in-progress state."""
        self._username: str | None = None
        self._password: str | None = None
        self._url: str = EU_URL
        self._pending_auth_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step: account credentials and region."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._url = user_input[CONF_URL]

            if self._url == OPTION_CUSTOM_URL:
                return await self.async_step_custom_url()

            errors = await self._async_try_login()
            if not errors:
                return await self._async_finish()
            if errors.get("base") == "mfa_required":
                return await self.async_step_mfa_code()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                    vol.Required(CONF_URL, default=EU_URL): vol.In(
                        [EU_URL, RUSSIA_URL, OPTION_CUSTOM_URL]
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_custom_url(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a custom region/API URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._url = user_input[CONF_URL]
            errors = await self._async_try_login()
            if not errors:
                return await self._async_finish()
            if errors.get("base") == "mfa_required":
                return await self.async_step_mfa_code()

        return self.async_show_form(
            step_id="custom_url",
            data_schema=vol.Schema({vol.Required(CONF_URL, default=self._url): str}),
            errors=errors,
        )

    async def async_step_mfa_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the MFA/SMS verification code step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = await self._async_try_login(sms_code=int(user_input["code"]))
            if not errors:
                return await self._async_finish()

        return self.async_show_form(
            step_id="mfa_code",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauthentication when the stored session has expired."""
        self._username = entry_data[CONF_USERNAME]
        self._url = entry_data[CONF_URL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauthentication with a fresh password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            errors = await self._async_try_login()
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data=self._pending_auth_data
                )
            if errors.get("base") == "mfa_required":
                return await self.async_step_mfa_code()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={CONF_USERNAME: self._username or ""},
        )

    async def _async_try_login(
        self, sms_code: int | None = None
    ) -> dict[str, str]:
        """Attempt a login with the currently stashed credentials.

        Stores the resulting token data on success; returns an errors dict
        (possibly with ``base: "mfa_required"``) on failure.
        """
        assert self._username is not None
        assert self._password is not None

        try:
            self._pending_auth_data = await self.hass.async_add_executor_job(
                _login, self._username, self._password, self._url, sms_code
            )
        except EzvizAuthVerificationCode:
            return {"base": "mfa_required"}
        except InvalidURL:
            return {"base": "invalid_host"}
        except InvalidHost:
            return {"base": "cannot_connect"}
        except PyEzvizError:
            return {"base": "invalid_auth"}
        except Exception:  # noqa: BLE001 - surface anything unexpected as a flow error
            _LOGGER.exception("Unexpected exception during EZVIZ login")
            return {"base": "unknown"}
        return {}

    async def _async_finish(self) -> ConfigFlowResult:
        """Create the config entry from a successful login."""
        assert self._username is not None
        await self.async_set_unique_id(self._username.lower())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=self._username, data=self._pending_auth_data
        )
