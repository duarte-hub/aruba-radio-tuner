"""State, scheduling, and safe restoration for Aruba radio comparisons."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any

from .api import ArubaApiError, ArubaInstantApi

_LOGGER = logging.getLogger(__name__)
UTC = timezone.utc
STATE_PATH = Path("/data/state.json")


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat()


class RunManager:
    """Own the active controller connection and persist runs between restarts."""

    def __init__(self, api: ArubaInstantApi) -> None:
        self.api = api
        self.data: dict[str, Any] = {
            "status": "connecting", "active_run": None, "aps": [], "results": [],
            "schedules": [], "recovery": None, "last_error": None, "api_version": "",
        }
        self.task: asyncio.Task[None] | None = None
        self.scheduler_task: asyncio.Task[None] | None = None
        self.refresh_task: asyncio.Task[None] | None = None
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        try:
            stored = json.loads(STATE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            stored = {}
        self.data["results"] = stored.get("results", [])[-30:]
        self.data["schedules"] = stored.get("schedules", [])[-100:]
        self.data["recovery"] = stored.get("recovery")
        for schedule in self.data["schedules"]:
            if schedule.get("status") == "running":
                schedule["status"] = "scheduled"
                schedule["run_at"] = iso_now()

        try:
            self.data["api_version"] = await self.api.async_get_version()
            self.data["aps"] = await self.api.async_get_aps()
            self.data["last_error"] = None
            if self.data.get("recovery"):
                await self.restore_recovery()
            self.data["status"] = "idle"
        except ArubaApiError as err:
            self.data["status"] = "offline"
            self.data["last_error"] = str(err)
            _LOGGER.warning("Aruba connection failed: %s", err)
        await self.save()
        self.scheduler_task = asyncio.create_task(self._scheduler_loop())
        self.refresh_task = asyncio.create_task(self._refresh_loop())

    async def close(self) -> None:
        for task in (self.scheduler_task, self.refresh_task):
            if task:
                task.cancel()
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        await self.api.async_logout()

    async def refresh(self) -> dict[str, Any]:
        if self.data.get("status") == "configuration_required":
            return self.public_state()
        try:
            self.data["aps"] = await self.api.async_get_aps()
            self.data["last_error"] = None
            if self.data["status"] == "offline":
                self.data["status"] = "idle"
        except ArubaApiError as err:
            self.data["last_error"] = str(err)
            self.data["status"] = "offline"
        return self.public_state()

    def public_state(self) -> dict[str, Any]:
        return {
            **self.data,
            "busy": bool(self.task and not self.task.done()),
        }

    async def start(self, config: dict[str, Any], *, scheduled: bool = False,
                    schedule_id: str | None = None) -> str:
        _validate_config(config)
        async with self.lock:
            if self.task and not self.task.done():
                raise ValueError("A radio test is already running")
            run_id = uuid.uuid4().hex[:10]
            self.task = asyncio.create_task(
                self._run(config, scheduled=scheduled, run_id=run_id, schedule_id=schedule_id)
            )
        return run_id

    async def cancel_test(self) -> None:
        if not self.task or self.task.done():
            raise ValueError("There is no running test")
        self.task.cancel()

    async def _run(self, config: dict[str, Any], *, scheduled: bool,
                   run_id: str, schedule_id: str | None = None) -> None:
        started = utcnow()
        ap_ip = str(config["ap_ip"])
        mode = str(config["mode"])
        original: dict[str, Any] | None = None
        results: list[dict[str, Any]] = []
        self.data.update(status="running", last_error=None)
        self.data["active_run"] = {
            "id": run_id, "mode": mode, "ap_ip": ap_ip, "scheduled": scheduled,
            "started_at": started.isoformat(), "completed": 0, "total": 0,
            "step": "Reading starting radio state",
        }
        try:
            aps = await self.api.async_get_aps()
            selected = next((ap for ap in aps if ap["ip"] == ap_ip), None)
            if selected is None:
                raise ArubaApiError(f"AP {ap_ip} was not found in the AP inventory")
            original = _radio_state(selected)
            if original.get("channel") is None or original.get("power_dbm") is None:
                raise ArubaApiError("Could not read the AP's current 2.4 GHz channel and power; no radio changes were made")
            ap_name = selected.get("name", "")
            baseline_clients = await self._read_clients(ap_ip, original["channel"], ap_name)
            baseline_macs = {str(row["mac"]).lower() for row in baseline_clients}
            conditions = _conditions(config, original)
            self.data["active_run"].update(total=len(conditions), baseline_clients=len(baseline_macs))
            self.data["recovery"] = {"ap_ip": ap_ip, "channel": original["channel"], "power_dbm": original["power_dbm"]}
            await self.save()

            for index, condition in enumerate(conditions, 1):
                self.data["active_run"].update(
                    completed=index - 1,
                    step=f"Channel {condition['channel']} · {condition['power_dbm']:g} dBm",
                )
                await self.api.async_set_24ghz(ap_ip, condition["channel"], condition["power_dbm"])
                sample = await self._wait_and_sample(
                    ap_ip, condition, baseline_macs, int(config["recovery_seconds"]), ap_name
                )
                results.append(sample)
                self.data["active_run"].update(completed=index, samples=results)

            recommendation = _recommend(results, baseline_macs)
            run_result = {
                "id": run_id, "ap_ip": ap_ip, "ap_name": selected.get("name", ap_ip),
                "mode": mode, "scheduled": scheduled, "started_at": started.isoformat(),
                "finished_at": iso_now(), "baseline_clients": len(baseline_macs),
                "samples": results, "recommendation": recommendation, "applied": False,
            }
            self.data["results"].insert(0, run_result)
            self.data["results"] = self.data["results"][:30]
            enough = recommendation["baseline_clients"] > 0 and recommendation["clients_returned"] >= math.ceil(recommendation["baseline_clients"] * 0.75)
            if scheduled and enough:
                await self.api.async_set_24ghz(ap_ip, int(recommendation["channel"]), float(recommendation["power_dbm"]))
                run_result["applied"] = True
                run_result["applied_at"] = iso_now()
                self._set_schedule_status(schedule_id, "complete")
            else:
                await self.api.async_set_24ghz(ap_ip, int(original["channel"]), float(original["power_dbm"]))
                if scheduled:
                    recommendation["reason"] += " Automatic apply was skipped because fewer than 75% of baseline clients returned; the starting radio setting was restored."
                    self._set_schedule_status(schedule_id, "review_required")
            self.data.update(recovery=None, active_run=None, status="idle")
            await self.refresh()
            await self.save()
        except asyncio.CancelledError:
            if original:
                await self._restore_state(ap_ip, original)
            self.data["active_run"] = None
            if self.data.get("status") != "restore_error":
                self.data["status"] = "cancelled"
            self._set_schedule_status(schedule_id, "cancelled")
            await self.save()
            raise
        except (ArubaApiError, ValueError, KeyError) as err:
            _LOGGER.exception("Aruba radio comparison failed")
            self.data.update(last_error=str(err), active_run=None, status="error")
            self._set_schedule_status(schedule_id, "error", str(err))
            if original:
                await self._restore_state(ap_ip, original)
            await self.save()

    async def _wait_and_sample(self, ap_ip: str, condition: dict[str, Any],
                               baseline_macs: set[str], timeout: int,
                               ap_name: str) -> dict[str, Any]:
        deadline = utcnow() + timedelta(seconds=timeout)
        selected: dict[str, Any] = {}
        clients: list[dict[str, Any]] = []
        while True:
            aps = await self.api.async_get_aps()
            selected = next((ap for ap in aps if ap["ip"] == ap_ip), {})
            clients = await self._read_clients(ap_ip, condition["channel"], ap_name)
            returned = len(baseline_macs & {str(row["mac"]).lower() for row in clients})
            required = math.ceil(len(baseline_macs) * 0.75) if baseline_macs else 0
            if returned >= required or utcnow() >= deadline:
                break
            await asyncio.sleep(min(15, max(1, (deadline - utcnow()).total_seconds())))
        actual = _radio_state(selected)
        return {
            "channel_requested": condition["channel"], "power_requested_dbm": condition["power_dbm"],
            "channel_live": actual.get("channel"), "power_live_dbm": actual.get("power_dbm"),
            "utilization_percent": actual.get("utilization_percent"), "noise_dbm": actual.get("noise_dbm"),
            "clients": clients, "client_count": len(clients),
            "baseline_clients_returned": len(baseline_macs & {str(row["mac"]).lower() for row in clients}),
            "captured_at": iso_now(),
        }

    async def _read_clients(self, ap_ip: str, channel: int | None,
                            ap_name: str = "") -> list[dict[str, Any]]:
        clients = await self.api.async_get_clients(ap_ip)
        return [row for row in clients if row.get("ap_ip") == ap_ip
                and (not ap_name or not row.get("ap") or row.get("ap") == ap_name)
                and (channel is None or str(row.get("channel")) == str(channel))]

    async def apply(self, run_id: str) -> None:
        result = next((item for item in self.data["results"] if item["id"] == run_id), None)
        if not result:
            raise ValueError("Result was not found")
        rec = result["recommendation"]
        await self.api.async_set_24ghz(result["ap_ip"], int(rec["channel"]), float(rec["power_dbm"]))
        result["applied"] = True
        result["applied_at"] = iso_now()
        await self.refresh()
        await self.save()

    async def schedule(self, config: dict[str, Any], run_at: str) -> str:
        _validate_config(config)
        when = _parse_datetime(run_at)
        if when <= utcnow():
            raise ValueError("Scheduled time must be in the future")
        schedule_id = uuid.uuid4().hex[:10]
        self.data["schedules"].append({"id": schedule_id, "run_at": when.isoformat(),
            "status": "scheduled", "config": config, "created_at": iso_now()})
        await self.save()
        return schedule_id

    async def cancel_schedule(self, schedule_id: str) -> None:
        schedule = next((item for item in self.data["schedules"]
                         if item["id"] == schedule_id and item.get("status") == "scheduled"), None)
        if not schedule:
            raise ValueError("Pending schedule was not found")
        schedule["status"] = "cancelled"
        await self.save()

    async def delete_result(self, run_id: str) -> None:
        self.data["results"] = [item for item in self.data["results"] if item["id"] != run_id]
        await self.save()

    async def restore_recovery(self) -> None:
        recovery = self.data.get("recovery")
        if not recovery:
            return
        try:
            await self.api.async_set_24ghz(recovery["ap_ip"], int(recovery["channel"]), float(recovery["power_dbm"]))
            self.data["recovery"] = None
        except (ArubaApiError, KeyError, ValueError):
            self.data["last_error"] = "A previous run was interrupted; automatic radio restore failed."
            _LOGGER.exception("Could not restore the 2.4 GHz radio after an interrupted run")
        await self.save()

    async def _restore_state(self, ap_ip: str, state: dict[str, Any]) -> None:
        try:
            await self.api.async_set_24ghz(ap_ip, int(state["channel"]), float(state["power_dbm"]))
            self.data["recovery"] = None
        except (ArubaApiError, KeyError, ValueError):
            self.data["last_error"] = "Could not restore the AP's original 2.4 GHz settings. Check the AP immediately."
            self.data["status"] = "restore_error"
            _LOGGER.exception("Could not restore the original radio setting")

    def _set_schedule_status(self, schedule_id: str | None, status: str,
                             error: str | None = None) -> None:
        if not schedule_id:
            return
        schedule = next((item for item in self.data["schedules"] if item["id"] == schedule_id), None)
        if schedule:
            schedule["status"] = status
            schedule["finished_at"] = iso_now()
            if error:
                schedule["error"] = error

    async def _scheduler_loop(self) -> None:
        while True:
            await asyncio.sleep(10)
            if self.data.get("status") == "restore_error" or (self.task and not self.task.done()):
                continue
            due = [item for item in self.data["schedules"]
                   if item.get("status") == "scheduled" and _parse_datetime(item.get("run_at")) <= utcnow()]
            if due:
                schedule = due[0]
                schedule["status"] = "running"
                schedule["started_at"] = iso_now()
                await self.save()
                await self.start(schedule["config"], scheduled=True, schedule_id=schedule["id"])

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(30)
            if not self.task or self.task.done():
                await self.refresh()

    async def save(self) -> None:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {key: self.data.get(key) for key in ("results", "schedules", "recovery")}
        temporary = STATE_PATH.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")))
        os.chmod(temporary, 0o600)
        temporary.replace(STATE_PATH)


def _conditions(config: dict[str, Any], original: dict[str, Any]) -> list[dict[str, Any]]:
    powers = [float(config.get("start_power_dbm", 8))]
    maximum = float(config.get("max_power_dbm", 13))
    while powers[-1] < maximum:
        powers.append(round(min(powers[-1] + float(config.get("step_dbm", 2)), maximum), 2))
    channels = list(dict.fromkeys(int(ch) for ch in config.get("channels", [1, 6, 11])))
    mode = config["mode"]
    if mode == "power":
        return [{"channel": int(original["channel"]), "power_dbm": p} for p in powers]
    if mode == "channels":
        power = float(config.get("channel_test_power_dbm", original["power_dbm"]))
        return [{"channel": ch, "power_dbm": power} for ch in channels]
    if mode == "combined":
        return [{"channel": ch, "power_dbm": p} for ch in channels for p in powers]
    raise ValueError("Test mode must be power, channels, or combined")


def _radio_state(ap: dict[str, Any]) -> dict[str, Any]:
    return {"channel": _as_int(ap.get("channel24")), "power_dbm": _as_float(ap.get("power24_dbm")),
            "utilization_percent": _as_float(ap.get("util24_percent")), "noise_dbm": _as_float(ap.get("noise24_dbm"))}


def _recommend(samples: list[dict[str, Any]], baseline_macs: set[str]) -> dict[str, Any]:
    if not samples:
        raise ValueError("No radio samples were collected")
    scored = []
    for sample in samples:
        macs = {str(row.get("mac", "")).lower() for row in sample.get("clients", [])}
        returned = len(macs & baseline_macs)
        fraction = returned / len(baseline_macs) if baseline_macs else 0
        clients = sample.get("clients", [])
        rates = [float(c["rate"]) for c in clients if c.get("rate") is not None]
        signals = [float(c["signal"]) for c in clients if c.get("signal") is not None]
        util = _as_float(sample.get("utilization_percent")) or 0
        noise = _as_float(sample.get("noise_dbm"))
        score = fraction * 50 + (min(30, median(rates) * .3) if rates else 0)
        score += min(20, max(0, ((median(signals) if signals else 0) / 50 * 20)))
        score -= min(15, util * .15) + min(10, max(0, ((noise if noise is not None else -100) + 90) * .5))
        scored.append((score, sample, returned))
    scored.sort(key=lambda row: (row[0], row[2], -float(row[1].get("power_live_dbm") or row[1]["power_requested_dbm"]), -int(row[1]["channel_requested"])), reverse=True)
    score, winner, returned = scored[0]
    return {"channel": winner.get("channel_live") or winner["channel_requested"],
        "power_dbm": winner.get("power_live_dbm") or winner["power_requested_dbm"],
        "score": round(score, 1), "clients_returned": returned, "baseline_clients": len(baseline_macs),
        "confidence": "client-observed" if baseline_macs else "radio-only; no baseline 2.4 GHz clients were connected",
        "reason": "Ranked by baseline client return, median PHY rate and signal, then lower utilization, noise floor, and transmit power."}


def _validate_config(config: dict[str, Any]) -> None:
    if config.get("mode") not in ("power", "channels", "combined"):
        raise ValueError("Choose power, channels, or combined")
    recovery = int(config.get("recovery_seconds", 120))
    channels = [int(item) for item in config.get("channels", [1, 6, 11])]
    start = float(config.get("start_power_dbm", 8))
    maximum = float(config.get("max_power_dbm", 13))
    step = float(config.get("step_dbm", 2))
    if not 0 <= recovery <= 900:
        raise ValueError("Recovery timeout must be between 0 and 900 seconds")
    if not channels or any(ch < 1 or ch > 14 for ch in channels):
        raise ValueError("Channels must be between 1 and 14")
    if step <= 0 or start < 0 or maximum < start or maximum > 30:
        raise ValueError("Power values must be valid; maximum must be at least the start and no higher than 30 dBm")
    if config["mode"] == "channels" and not 0 <= float(config.get("channel_test_power_dbm", 13)) <= 30:
        raise ValueError("Channel test power must be between 0 and 30 dBm")
    power_count = math.ceil((maximum - start) / step) + 1
    count = power_count if config["mode"] == "power" else len(channels) if config["mode"] == "channels" else len(channels) * power_count
    if count > 120:
        raise ValueError("This run exceeds 120 conditions; reduce the range or number of channels")


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        raise ValueError("A scheduled date and time is required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


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
