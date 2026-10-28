"""Run, schedule, persist, and score radio comparisons."""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .api import ArubaApiError, ArubaInstantApi
from .const import STORAGE_KEY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class ArubaRunManager:
    """Own one cluster connection's tests and schedule queue."""

    def __init__(self, hass: HomeAssistant, api: ArubaInstantApi, entry_id: str) -> None:
        self.hass = hass
        self.api = api
        self.entry_id = entry_id
        self.store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        self.data: dict[str, Any] = {
            "entry_id": entry_id,
            "status": "idle",
            "active_run": None,
            "aps": [],
            "results": [],
            "schedules": [],
            "recovery": None,
            "last_error": None,
            "api_version": "",
        }
        self._sensor_update: Callable[[], None] | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._schedule_cancel: Callable[[], None] | None = None
        self._refresh_cancel: Callable[[], None] | None = None

    async def async_load(self) -> None:
        stored = await self.store.async_load()
        if stored:
            self.data["results"] = stored.get("results", [])
            self.data["schedules"] = stored.get("schedules", [])
            self.data["recovery"] = stored.get("recovery")
            # A Home Assistant restart interrupts a scheduled task. Requeue it from its
            # beginning after restoring the last saved 2.4 GHz state.
            for schedule in self.data["schedules"]:
                if schedule.get("status") == "running":
                    schedule["status"] = "scheduled"
                    schedule["run_at"] = dt_util.utcnow().isoformat()
        if self.data.get("recovery"):
            await self._async_restore_recovery()
        if stored:
            await self._async_save()
        try:
            self.data["api_version"] = await self.api.async_get_version()
            await self.async_refresh()
        except ArubaApiError as err:
            self.data["last_error"] = str(err)
            _LOGGER.warning("Aruba API initial refresh failed: %s", err)
        self._schedule_cancel = async_track_time_interval(
            self.hass, self._async_tick, timedelta(seconds=15)
        )
        self._refresh_cancel = async_track_time_interval(
            self.hass, self._async_periodic_refresh, timedelta(seconds=30)
        )
        self._notify()

    async def async_unload(self) -> None:
        if self._schedule_cancel:
            self._schedule_cancel()
        if self._refresh_cancel:
            self._refresh_cancel()
        if self._run_task and not self._run_task.done():
            self._run_task.cancel()
            try:
                await self._run_task
            except asyncio.CancelledError:
                pass
        await self.api.async_logout()

    def set_sensor_update(self, callback: Callable[[], None]) -> None:
        self._sensor_update = callback
        self._notify()

    def _notify(self) -> None:
        if self._sensor_update:
            self._sensor_update()

    async def async_refresh(self) -> None:
        try:
            self.data["aps"] = await self.api.async_get_aps()
            self.data["last_error"] = None
        except ArubaApiError as err:
            self.data["last_error"] = str(err)
            _LOGGER.debug("Aruba AP refresh failed: %s", err)
        self._notify()

    async def _async_periodic_refresh(self, _now: datetime) -> None:
        if self.data.get("recovery"):
            await self._async_restore_recovery()
        if not self._run_task or self._run_task.done():
            await self.async_refresh()

    async def _async_tick(self, now: datetime) -> None:
        if self.data.get("recovery"):
            return
        due = [
            item
            for item in self.data["schedules"]
            if item.get("status") == "scheduled"
            and _parse_datetime(item.get("run_at")) <= now
        ]
        if due and (not self._run_task or self._run_task.done()):
            schedule = due[0]
            schedule["status"] = "running"
            schedule["started_at"] = dt_util.utcnow().isoformat()
            await self._async_save()
            self._run_task = self.hass.async_create_task(
                self._async_run(schedule["config"], scheduled=True, schedule_id=schedule["id"])
            )

    def start_test(self, config: dict[str, Any], *, scheduled: bool = False) -> str:
        if self._run_task and not self._run_task.done():
            raise ValueError("A radio test is already running")
        _validate_config(config)
        run_id = uuid.uuid4().hex[:10]
        self._run_task = self.hass.async_create_task(
            self._async_run(config, scheduled=scheduled, run_id=run_id)
        )
        return run_id

    async def _async_run(
        self,
        config: dict[str, Any],
        *,
        scheduled: bool,
        run_id: str | None = None,
        schedule_id: str | None = None,
    ) -> None:
        run_id = run_id or schedule_id or uuid.uuid4().hex[:10]
        started = dt_util.utcnow()
        ap_ip = str(config["ap_ip"])
        mode = str(config["mode"])
        results: list[dict[str, Any]] = []
        original: dict[str, Any] | None = None
        self.data["status"] = "running"
        self.data["last_error"] = None
        self.data["active_run"] = {
            "id": run_id,
            "mode": mode,
            "ap_ip": ap_ip,
            "scheduled": scheduled,
            "started_at": started.isoformat(),
            "completed": 0,
            "total": 0,
            "step": "Reading starting radio state",
        }
        self._notify()

        try:
            aps = await self.api.async_get_aps()
            selected = next((ap for ap in aps if ap["ip"] == ap_ip), None)
            if selected is None:
                raise ArubaApiError(f"AP {ap_ip} was not found in show aps")
            original = _radio_state(selected)
            if original.get("channel") is None or original.get("power_dbm") is None:
                raise ArubaApiError(
                    "Could not read the AP's current 2.4 GHz channel and power; no radio changes were made"
                )
            ap_name = selected.get("name", "")
            baseline_clients = await self._async_read_clients(ap_ip, original.get("channel"), ap_name)
            baseline_macs = {str(row["mac"]).lower() for row in baseline_clients}
            conditions = _conditions(config, original)
            self.data["active_run"]["total"] = len(conditions)
            self.data["active_run"]["baseline_clients"] = len(baseline_macs)
            self.data["recovery"] = {
                "ap_ip": ap_ip,
                "channel": int(original["channel"]),
                "power_dbm": float(original["power_dbm"]),
            }
            await self._async_save()
            self._notify()

            for index, condition in enumerate(conditions, start=1):
                self.data["active_run"].update(
                    {
                        "completed": index - 1,
                        "step": f"Channel {condition['channel']} · {condition['power_dbm']:g} dBm",
                    }
                )
                self._notify()
                await self.api.async_set_24ghz(ap_ip, condition["channel"], condition["power_dbm"])
                sample = await self._async_wait_and_sample(
                    ap_ip,
                    condition,
                    baseline_macs,
                    int(config["recovery_seconds"]),
                    ap_name,
                )
                results.append(sample)
                self.data["active_run"]["completed"] = index
                self.data["active_run"]["samples"] = results
                self._notify()

            recommendation = _recommend(results, baseline_macs)
            run_result = {
                "id": run_id,
                "ap_ip": ap_ip,
                "ap_name": selected.get("name", ap_ip),
                "mode": mode,
                "scheduled": scheduled,
                "started_at": started.isoformat(),
                "finished_at": dt_util.utcnow().isoformat(),
                "baseline_clients": len(baseline_macs),
                "samples": results,
                "recommendation": recommendation,
                "applied": False,
            }
            self.data["results"].insert(0, run_result)
            self.data["results"] = self.data["results"][:10]
            if schedule_id:
                schedule = next((item for item in self.data["schedules"] if item["id"] == schedule_id), None)
                if schedule:
                    schedule["status"] = "complete"
                    schedule["finished_at"] = run_result["finished_at"]

            enough_clients = recommendation["clients_returned"] >= math.ceil(
                recommendation["baseline_clients"] * 0.75
            ) and recommendation["baseline_clients"] > 0
            if scheduled and enough_clients:
                await self.api.async_set_24ghz(
                    ap_ip,
                    int(recommendation["channel"]),
                    float(recommendation["power_dbm"]),
                )
                run_result["applied"] = True
                self.data["active_run"]["step"] = "Applying recommended setting"
            elif original.get("channel") and original.get("power_dbm") is not None:
                # Normal runs are exploratory. Restore the starting setting until the user applies a result.
                await self.api.async_set_24ghz(
                    ap_ip, int(original["channel"]), float(original["power_dbm"])
                )
                if scheduled and schedule_id:
                    schedule = next((item for item in self.data["schedules"] if item["id"] == schedule_id), None)
                    if schedule:
                        schedule["status"] = "review_required"
                    run_result["recommendation"]["reason"] += " The run did not recover enough baseline clients to auto-apply, so it restored the starting setting and left the recommendation for review."
            self.data["recovery"] = None
            self.data["active_run"] = None
            self.data["status"] = "idle"
            await self.async_refresh()
            await self._async_save()
        except asyncio.CancelledError:
            if original and original.get("channel") and original.get("power_dbm") is not None:
                try:
                    await self.api.async_set_24ghz(ap_ip, int(original["channel"]), float(original["power_dbm"]))
                    self.data["recovery"] = None
                except ArubaApiError:
                    _LOGGER.exception("Could not restore radio after cancelling test")
            self.data["status"] = "cancelled"
            self.data["active_run"] = None
            if schedule_id:
                schedule = next((item for item in self.data["schedules"] if item["id"] == schedule_id), None)
                if schedule:
                    schedule["status"] = "cancelled"
            await self._async_save()
            raise
        except (ArubaApiError, ValueError, KeyError) as err:
            self.data["last_error"] = str(err)
            self.data["status"] = "error"
            self.data["active_run"] = None
            if schedule_id:
                schedule = next((item for item in self.data["schedules"] if item["id"] == schedule_id), None)
                if schedule:
                    schedule["status"] = "error"
                    schedule["error"] = str(err)
            if original and original.get("channel") and original.get("power_dbm") is not None:
                try:
                    await self.api.async_set_24ghz(ap_ip, int(original["channel"]), float(original["power_dbm"]))
                    self.data["recovery"] = None
                except ArubaApiError:
                    _LOGGER.exception("Could not restore radio after failed test")
            _LOGGER.exception("Aruba radio comparison failed: %s", err)
            await self._async_save()
        finally:
            self._notify()

    async def _async_read_clients(
        self, ap_ip: str, channel: int | None, ap_name: str = ""
    ) -> list[dict[str, Any]]:
        clients = await self.api.async_get_clients(ap_ip)
        return [
            row for row in clients
            if row.get("ap_ip") == ap_ip
            and (not ap_name or not row.get("ap") or row.get("ap") == ap_name)
            and (channel is None or str(row.get("channel")) == str(channel))
        ]

    async def _async_wait_and_sample(
        self,
        ap_ip: str,
        condition: dict[str, Any],
        baseline_macs: set[str],
        recovery_seconds: int,
        ap_name: str,
    ) -> dict[str, Any]:
        deadline = dt_util.utcnow() + timedelta(seconds=recovery_seconds)
        clients: list[dict[str, Any]] = []
        while True:
            aps = await self.api.async_get_aps()
            selected = next((ap for ap in aps if ap["ip"] == ap_ip), {})
            clients = await self._async_read_clients(ap_ip, condition["channel"], ap_name)
            returned = len(baseline_macs & {str(row["mac"]).lower() for row in clients})
            required = math.ceil(len(baseline_macs) * 0.75) if baseline_macs else 0
            if returned >= required and (recovery_seconds == 0 or dt_util.utcnow() >= deadline - timedelta(seconds=min(30, recovery_seconds))):
                break
            if dt_util.utcnow() >= deadline:
                break
            await asyncio.sleep(min(15, max(1, (deadline - dt_util.utcnow()).total_seconds())))
        actual = _radio_state(selected)
        return {
            "channel_requested": condition["channel"],
            "power_requested_dbm": condition["power_dbm"],
            "channel_live": actual.get("channel"),
            "power_live_dbm": actual.get("power_dbm"),
            "utilization_percent": actual.get("utilization_percent"),
            "noise_dbm": actual.get("noise_dbm"),
            "clients": clients,
            "client_count": len(clients),
            "baseline_clients_returned": len(baseline_macs & {str(row["mac"]).lower() for row in clients}),
            "captured_at": dt_util.utcnow().isoformat(),
        }

    async def apply_recommendation(self, run_id: str) -> None:
        result = next((item for item in self.data["results"] if item["id"] == run_id), None)
        if not result:
            raise ValueError("Result was not found")
        recommendation = result["recommendation"]
        await self.api.async_set_24ghz(
            result["ap_ip"], int(recommendation["channel"]), float(recommendation["power_dbm"])
        )
        result["applied"] = True
        result["applied_at"] = dt_util.utcnow().isoformat()
        await self.async_refresh()
        await self._async_save()

    async def schedule_test(self, config: dict[str, Any], run_at: str) -> str:
        _validate_config(config)
        when = _parse_datetime(run_at)
        if when <= dt_util.utcnow():
            raise ValueError("Scheduled time must be in the future")
        schedule_id = uuid.uuid4().hex[:10]
        self.data["schedules"].append(
            {
                "id": schedule_id,
                "run_at": when.isoformat(),
                "status": "scheduled",
                "config": config,
                "created_at": dt_util.utcnow().isoformat(),
            }
        )
        await self._async_save()
        self._notify()
        return schedule_id

    async def cancel_schedule(self, schedule_id: str) -> None:
        for schedule in self.data["schedules"]:
            if schedule["id"] == schedule_id and schedule.get("status") == "scheduled":
                schedule["status"] = "cancelled"
                await self._async_save()
                self._notify()
                return
        raise ValueError("Pending schedule was not found")

    async def delete_result(self, run_id: str) -> None:
        self.data["results"] = [item for item in self.data["results"] if item["id"] != run_id]
        await self._async_save()
        self._notify()

    async def _async_save(self) -> None:
        await self.store.async_save(
            {
                "results": self.data["results"],
                "schedules": self.data["schedules"],
                "recovery": self.data.get("recovery"),
            }
        )
        self._notify()

    async def _async_restore_recovery(self) -> None:
        """Restore a radio snapshot left by a Home Assistant restart."""
        recovery = self.data.get("recovery")
        if not recovery:
            return
        try:
            await self.api.async_set_24ghz(
                recovery["ap_ip"], int(recovery["channel"]), float(recovery["power_dbm"])
            )
        except (ArubaApiError, KeyError, ValueError):
            self.data["last_error"] = "A previous run was interrupted; automatic radio restore failed."
            _LOGGER.exception("Could not restore 2.4 GHz radio state after Home Assistant restart")
            return
        self.data["recovery"] = None
        await self._async_save()


def _conditions(config: dict[str, Any], original: dict[str, Any]) -> list[dict[str, Any]]:
    mode = config["mode"]
    powers = [float(config["start_power_dbm"])]
    maximum = float(config["max_power_dbm"])
    while powers[-1] < maximum:
        powers.append(round(min(powers[-1] + float(config["step_dbm"]), maximum), 2))
    channels = [int(ch) for ch in config.get("channels", [1, 6, 11])]
    if mode == "power":
        channel = int(original.get("channel") or channels[0])
        return [{"channel": channel, "power_dbm": power} for power in powers]
    if mode == "channels":
        power = float(config.get("channel_test_power_dbm", original.get("power_dbm") or 13))
        return [{"channel": channel, "power_dbm": power} for channel in channels]
    if mode == "combined":
        return [{"channel": channel, "power_dbm": power} for channel in channels for power in powers]
    raise ValueError("Mode must be power, channels, or combined")


def _radio_state(ap: dict[str, Any]) -> dict[str, Any]:
    return {
        "channel": _as_int(ap.get("channel24")),
        "power_dbm": _as_float(ap.get("power24_dbm")),
        "utilization_percent": _as_float(ap.get("util24_percent")),
        "noise_dbm": _as_float(ap.get("noise24_dbm")),
    }


def _recommend(samples: list[dict[str, Any]], baseline_macs: set[str]) -> dict[str, Any]:
    if not samples:
        raise ValueError("No radio samples were collected")
    candidates = []
    for sample in samples:
        clients = sample.get("clients", [])
        macs = {str(row.get("mac", "")).lower() for row in clients}
        returned = len(macs & baseline_macs)
        fraction = returned / len(baseline_macs) if baseline_macs else 0.0
        rates = [float(row["rate"]) for row in clients if row.get("rate") is not None]
        signals = [float(row["signal"]) for row in clients if row.get("signal") is not None]
        util = _as_float(sample.get("utilization_percent"))
        rate_score = min(30.0, (median(rates) / 100.0 * 30.0)) if rates else 0.0
        signal_score = min(20.0, max(0.0, ((median(signals) if signals else 0.0) / 50.0 * 20.0)))
        utilization_penalty = min(15.0, (util or 0.0) * 0.15)
        noise = _as_float(sample.get("noise_dbm"))
        noise_penalty = min(10.0, max(0.0, ((noise or -100.0) + 90.0) * 0.5))
        score = fraction * 50.0 + rate_score + signal_score - utilization_penalty - noise_penalty
        candidates.append((score, sample, returned))
    candidates.sort(
        key=lambda row: (
            row[0],
            row[2],
            -float(row[1].get("power_live_dbm") or row[1]["power_requested_dbm"]),
            -int(row[1]["channel_requested"]),
        ),
        reverse=True,
    )
    score, winner, returned = candidates[0]
    return {
        "channel": winner.get("channel_live") or winner["channel_requested"],
        "power_dbm": winner.get("power_live_dbm") or winner["power_requested_dbm"],
        "score": round(score, 1),
        "clients_returned": returned,
        "baseline_clients": len(baseline_macs),
        "confidence": "client-observed" if baseline_macs else "radio-only; no baseline 2.4 GHz clients were connected",
        "reason": "Ranked by client return, median PHY rate and signal, then lower utilization, noise floor, and transmit power.",
    }


def _validate_config(config: dict[str, Any]) -> None:
    mode = config.get("mode")
    if mode not in ("power", "channels", "combined"):
        raise ValueError("Choose power, channels, or combined")
    recovery = int(config.get("recovery_seconds", 120))
    if not 0 <= recovery <= 900:
        raise ValueError("Recovery timeout must be between 0 and 900 seconds")
    channels = [int(item) for item in config.get("channels", [1, 6, 11])]
    if not channels or any(item < 1 or item > 14 for item in channels):
        raise ValueError("Channels must be between 1 and 14")
    start = float(config.get("start_power_dbm", 8))
    maximum = float(config.get("max_power_dbm", 13))
    step = float(config.get("step_dbm", 2))
    if step <= 0 or start < 0 or maximum < start or maximum > 30:
        raise ValueError("Power settings must be positive; maximum must be at least the start and no higher than 30 dBm")
    if mode == "channels":
        power = float(config.get("channel_test_power_dbm", 13))
        if not 0 <= power <= 30:
            raise ValueError("Channel test power must be between 0 and 30 dBm")
    power_count = math.ceil((maximum - start) / step) + 1
    condition_count = (
        power_count if mode == "power"
        else len(channels) if mode == "channels"
        else len(channels) * power_count
    )
    if condition_count > 120:
        raise ValueError("This run exceeds 120 conditions. Reduce channels or the power range, or increase the power step.")


def _parse_datetime(value: str | datetime | None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        parsed = dt_util.parse_datetime(str(value))
    else:
        parsed = None
    if parsed is None:
        raise ValueError("A valid scheduled date and time is required")
    return dt_util.as_utc(parsed)


def _as_int(value: Any) -> int | None:
    try:
        return int(float(str(value).replace(" dBm", "").replace("%", "")))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        return float(str(value).replace(" dBm", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
