"""Aruba Instant radio comparison integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import voluptuous as vol
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ArubaInstantApi
from .const import (
    CONF_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_APPLY,
    SERVICE_CANCEL_SCHEDULE,
    SERVICE_CANCEL_TEST,
    SERVICE_DELETE_RESULT,
    SERVICE_SCHEDULE,
    SERVICE_START_TEST,
    STATIC_URL,
)
from .manager import ArubaRunManager


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register services independent of config-entry setup."""
    hass.data.setdefault(DOMAIN, {})

    def manager_for(call: ServiceCall) -> ArubaRunManager:
        entry_id = call.data.get("config_entry_id")
        manager = hass.data[DOMAIN].get(entry_id)
        if manager is None:
            raise vol.Invalid("The selected Aruba connection is not loaded")
        return manager

    async def start_test(call: ServiceCall) -> None:
        manager_for(call).start_test(_test_config(call.data))

    async def schedule_test(call: ServiceCall) -> None:
        manager = manager_for(call)
        await manager.schedule_test(_test_config(call.data), call.data["run_at"])

    async def apply_recommendation(call: ServiceCall) -> None:
        await manager_for(call).apply_recommendation(call.data["run_id"])

    async def cancel_test(call: ServiceCall) -> None:
        manager = manager_for(call)
        if manager._run_task and not manager._run_task.done():
            manager._run_task.cancel()

    async def cancel_schedule(call: ServiceCall) -> None:
        await manager_for(call).cancel_schedule(call.data["schedule_id"])

    async def delete_result(call: ServiceCall) -> None:
        await manager_for(call).delete_result(call.data["run_id"])

    common = {vol.Required("config_entry_id"): str}
    test_fields = {
        **common,
        vol.Required("ap_ip"): str,
        vol.Required("mode"): vol.In(["power", "channels", "combined"]),
        vol.Optional("start_power_dbm", default=8): vol.Coerce(float),
        vol.Optional("max_power_dbm", default=13): vol.Coerce(float),
        vol.Optional("step_dbm", default=2): vol.Coerce(float),
        vol.Optional("channel_test_power_dbm", default=13): vol.Coerce(float),
        vol.Optional("channels", default=[1, 6, 11]): [vol.Coerce(int)],
        vol.Optional("recovery_seconds", default=120): vol.Coerce(int),
    }
    schemas = {
        SERVICE_START_TEST: vol.Schema(test_fields),
        SERVICE_SCHEDULE: vol.Schema({**test_fields, vol.Required("run_at"): str}),
        SERVICE_APPLY: vol.Schema({**common, vol.Required("run_id"): str}),
        SERVICE_CANCEL_TEST: vol.Schema(common),
        SERVICE_CANCEL_SCHEDULE: vol.Schema({**common, vol.Required("schedule_id"): str}),
        SERVICE_DELETE_RESULT: vol.Schema({**common, vol.Required("run_id"): str}),
    }
    handlers = {
        SERVICE_START_TEST: start_test,
        SERVICE_SCHEDULE: schedule_test,
        SERVICE_APPLY: apply_recommendation,
        SERVICE_CANCEL_TEST: cancel_test,
        SERVICE_CANCEL_SCHEDULE: cancel_schedule,
        SERVICE_DELETE_RESULT: delete_result,
    }
    for name, handler in handlers.items():
        hass.services.async_register(DOMAIN, name, handler, schema=schemas[name])
    return True


def _test_config(data: dict[str, Any]) -> dict[str, Any]:
    """Extract only supported test options from a service payload."""
    return {
        "ap_ip": data["ap_ip"],
        "mode": data["mode"],
        "start_power_dbm": data.get("start_power_dbm", 8),
        "max_power_dbm": data.get("max_power_dbm", 13),
        "step_dbm": data.get("step_dbm", 2),
        "channel_test_power_dbm": data.get("channel_test_power_dbm", 13),
        "channels": data.get("channels", [1, 6, 11]),
        "recovery_seconds": data.get("recovery_seconds", 120),
    }


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the Instant API manager and frontend assets."""
    api = ArubaInstantApi(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_VERIFY_SSL, False),
        entry.data.get("port", 4343),
    )
    manager = ArubaRunManager(hass, api, entry.entry_id)
    hass.data[DOMAIN][entry.entry_id] = manager
    await manager.async_load()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.data[DOMAIN].get("static_registered"):
        static_folder = Path(__file__).parent / "static"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(static_folder), False)]
        )
        hass.data[DOMAIN]["static_registered"] = True
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload one cluster manager."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        manager: ArubaRunManager = hass.data[DOMAIN].pop(entry.entry_id)
        await manager.async_unload()
    return unloaded
