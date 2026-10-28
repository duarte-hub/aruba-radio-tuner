"""Config flow for Aruba Radio Tuner."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArubaApiError, ArubaInstantApi
from .const import CONF_VERIFY_SSL, DEFAULT_PORT, DOMAIN


class ArubaRadioTunerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure an Aruba Instant conductor API connection."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            await self.async_set_unique_id(host.lower())
            self._abort_if_unique_id_configured()
            api = ArubaInstantApi(
                async_get_clientsession(self.hass),
                host,
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input[CONF_VERIFY_SSL],
                DEFAULT_PORT,
            )
            try:
                await api.async_login()
                version = await api.async_get_version()
                if not version:
                    raise ArubaApiError("The AP returned an empty version response")
                return self.async_create_entry(
                    title=f"Aruba Instant ({host})",
                    data={**user_input, "host": host, "port": DEFAULT_PORT, "api_version": version},
                )
            except ArubaApiError as err:
                message = str(err).lower()
                if "login" in message or "credential" in message:
                    errors["base"] = "invalid_auth"
                elif "not enabled" in message or "sid" in message:
                    errors["base"] = "api_not_enabled"
                else:
                    errors["base"] = "cannot_connect"
            finally:
                await api.async_logout()

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Optional(CONF_VERIFY_SSL, default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
