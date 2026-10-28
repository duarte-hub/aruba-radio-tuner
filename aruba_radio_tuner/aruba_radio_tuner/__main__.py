"""Run the Aruba tuner web application."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, web

from .api import ArubaApiError, ArubaInstantApi
from .manager import RunManager

_LOGGER = logging.getLogger("aruba_radio_tuner")
ROOT = Path(__file__).parent


@web.middleware
async def ingress_only(request: web.Request, handler):
    """Only accept requests forwarded by Home Assistant Ingress."""
    if request.remote != "172.30.32.2":
        raise web.HTTPForbidden(text="Open Aruba Radio Tuner through Home Assistant Apps.")
    return await handler(request)


async def _on_startup(app: web.Application) -> None:
    try:
        options: dict[str, Any] = json.loads(Path("/data/options.json").read_text())
    except (OSError, json.JSONDecodeError):
        options = {}
    app["session"] = ClientSession()
    api = ArubaInstantApi(
        app["session"], str(options.get("host", "192.168.0.25")),
        str(options.get("username", "")), str(options.get("password", "")),
        bool(options.get("verify_ssl", False)),
    )
    app["manager"] = RunManager(api)
    if not options.get("username") or not options.get("password"):
        app["manager"].data.update(status="configuration_required", last_error="Set the Instant API username and password in this app's Configuration tab, then restart the app.")
    else:
        await app["manager"].load()


async def _on_cleanup(app: web.Application) -> None:
    manager: RunManager | None = app.get("manager")
    if manager:
        await manager.close()
    session: ClientSession | None = app.get("session")
    if session:
        await session.close()


async def index(_request: web.Request) -> web.Response:
    return web.FileResponse(ROOT / "static" / "index.html")


async def static_file(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name not in {"app.js", "style.css"}:
        raise web.HTTPNotFound()
    return web.FileResponse(ROOT / "static" / name)


def manager(request: web.Request) -> RunManager:
    return request.app["manager"]


async def get_state(request: web.Request) -> web.Response:
    state = await manager(request).refresh()
    return web.json_response(state)


async def start_test(request: web.Request) -> web.Response:
    data = await request.json()
    run_id = await manager(request).start(data)
    return web.json_response({"run_id": run_id}, status=202)


async def schedule_test(request: web.Request) -> web.Response:
    data = await request.json()
    run_at = data.pop("run_at", None)
    schedule_id = await manager(request).schedule(data, run_at)
    return web.json_response({"schedule_id": schedule_id}, status=201)


async def apply_result(request: web.Request) -> web.Response:
    await manager(request).apply(request.match_info["run_id"])
    return web.json_response({"ok": True})


async def cancel_test(request: web.Request) -> web.Response:
    await manager(request).cancel_test()
    return web.json_response({"ok": True})


async def cancel_schedule(request: web.Request) -> web.Response:
    await manager(request).cancel_schedule(request.match_info["schedule_id"])
    return web.json_response({"ok": True})


async def delete_result(request: web.Request) -> web.Response:
    await manager(request).delete_result(request.match_info["run_id"])
    return web.json_response({"ok": True})


def create_app() -> web.Application:
    app = web.Application(middlewares=[ingress_only, _error_middleware])
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    app.router.add_get("/", index)
    app.router.add_get("/static/{name}", static_file)
    app.router.add_get("/api/state", get_state)
    app.router.add_post("/api/run", start_test)
    app.router.add_post("/api/schedule", schedule_test)
    app.router.add_post("/api/cancel", cancel_test)
    app.router.add_post("/api/results/{run_id}/apply", apply_result)
    app.router.add_delete("/api/results/{run_id}", delete_result)
    app.router.add_delete("/api/schedules/{schedule_id}", cancel_schedule)
    return app


@web.middleware
async def _error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except (ValueError, KeyError, ArubaApiError) as err:
        _LOGGER.info("Request rejected: %s", err)
        raise web.HTTPBadRequest(text=json.dumps({"error": str(err)}), content_type="application/json") from err
    except Exception as err:  # Keep UI usable while logging details in the app log.
        _LOGGER.exception("Unhandled request error")
        raise web.HTTPInternalServerError(text=json.dumps({"error": "Unexpected app error"}), content_type="application/json") from err


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    web.run_app(create_app(), host="0.0.0.0", port=8099, access_log=None)


if __name__ == "__main__":
    main()
