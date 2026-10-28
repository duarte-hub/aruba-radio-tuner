"""Small client for the Aruba Instant REST API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)


class ArubaApiError(Exception):
    """Raised when the Instant REST API returns an error."""


class ArubaInstantApi:
    """Authenticated REST client for an Aruba Instant conductor."""

    def __init__(
        self,
        session: ClientSession,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        port: int = 4343,
    ) -> None:
        self._session = session
        self.host = host.strip().rstrip("/")
        self.username = username
        self.password = password
        self.verify_ssl = verify_ssl
        self.port = port
        self.sid: str | None = None
        self.base_url = f"https://{self.host}:{self.port}"
        self._lock = asyncio.Lock()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=json,
                ssl=self.verify_ssl,
                timeout=ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                body = await response.json(content_type=None)
        except (ClientError, asyncio.TimeoutError, ValueError) as err:
            raise ArubaApiError(f"Instant API request failed: {err}") from err
        if not isinstance(body, dict):
            raise ArubaApiError("Instant API returned an unexpected response")
        return body

    async def async_login(self) -> str:
        """Log in and return the session ID."""
        async with self._lock:
            response = await self._request(
                "POST",
                "/rest/login",
                json={"user": self.username, "passwd": self.password},
            )
            sid = response.get("sid")
            if response.get("Status") != "Success" or not sid:
                message = response.get("Error message") or response.get("message") or "Login failed"
                raise ArubaApiError(str(message))
            self.sid = str(sid)
            return self.sid

    async def async_logout(self) -> None:
        """End the current session, if one exists."""
        if not self.sid:
            return
        sid, self.sid = self.sid, None
        try:
            await self._request("POST", "/rest/logout", json={"sid": sid})
        except ArubaApiError:
            _LOGGER.debug("Could not explicitly log out from Instant API", exc_info=True)

    async def _ensure_login(self) -> str:
        if not self.sid:
            await self.async_login()
        assert self.sid
        return self.sid

    async def async_show(self, ap_ip: str, command: str) -> str:
        """Run one supported read-only show command on an AP."""
        sid = await self._ensure_login()
        response = await self._request(
            "GET",
            "/rest/show-cmd",
            params={"iap_ip_addr": ap_ip, "cmd": command, "sid": sid},
        )
        if response.get("Status") != "Success" and response.get("Status-code") != 0:
            if response.get("Status-code") == 1:
                self.sid = None
                sid = await self._ensure_login()
                response = await self._request(
                    "GET",
                    "/rest/show-cmd",
                    params={"iap_ip_addr": ap_ip, "cmd": command, "sid": sid},
                )
            if response.get("Status") != "Success" and response.get("Status-code") != 0:
                raise ArubaApiError(str(response.get("Error message") or response.get("message") or "Show command failed"))
        output = response.get("Command output", "")
        return str(output).replace("\\n", "\n").replace("\\r", "\r")

    async def async_get_version(self) -> str:
        """Return the conductor version string."""
        output = await self.async_show(self.host, "show version")
        return output.strip()

    async def async_get_aps(self) -> list[dict[str, str]]:
        """Read AP inventory and radio telemetry from `show aps`."""
        output = await self.async_show(self.host, "show aps")
        return parse_ap_inventory(output)

    async def async_get_clients(self, ap_ip: str) -> list[dict[str, Any]]:
        """Read client association and link metrics from `show clients`."""
        output = await self.async_show(ap_ip, "show clients")
        return [{**row, "ap_ip": ap_ip} for row in parse_client_table(output)]

    async def async_set_24ghz(self, ap_ip: str, channel: int, power_dbm: float) -> None:
        """Set only the target AP's 2.4 GHz channel and transmit power."""
        sid = await self._ensure_login()
        payload = {
            "iap_ip_addr": ap_ip,
            "channel": {
                "g-channel": {
                    "channel_name": str(channel),
                    "tx_power": _format_power(power_dbm),
                }
            },
        }
        response = await self._request(
            "POST", "/rest/channel", params={"sid": sid}, json=payload
        )
        if not (
            response.get("Status") in ("Success", 0)
            or response.get("Status-code") == 0
        ):
            raise ArubaApiError(str(response.get("Error message") or response.get("message") or "Radio update failed"))


def _format_power(power: float) -> str:
    return str(int(power)) if float(power).is_integer() else str(power)


def _clean_cli(output: str) -> str:
    text = output.replace("\r", "\n")
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def parse_ap_inventory(output: str) -> list[dict[str, str]]:
    """Parse AP name/IP/client-count rows; retain raw output for future parsers."""
    import re

    text = _clean_cli(output)
    pattern = re.compile(
        r"^(?P<name>[^\s]+)\s+(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\s+"
        r"(?P<mode>access|mesh)\s+(?P<spectrum>\S+)\s+(?P<clients>\d+)\s+(?P<model>\S+)",
        re.IGNORECASE,
    )
    aps: list[dict[str, str]] = []
    starts: list[tuple[int, Any]] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        starts.append((index, match))
    for start_index, (index, match) in enumerate(starts):
        name, ip = match.group("name"), match.group("ip")
        if any(ap["ip"] == ip for ap in aps):
            continue
        end = starts[start_index + 1][0] if start_index + 1 < len(starts) else min(index + 8, len(lines))
        tail = " ".join(lines[index:end])
        fields = tail.split()
        serial_i = next(
            (i for i, token in enumerate(fields) if re.fullmatch(r"(?i)[A-Z0-9]{10}", token)),
            None,
        )
        radio = {}
        if serial_i is not None:
            values = fields[serial_i + 1 :]
            if len(values) >= 8:
                radio = {
                    "channel24": values[4],
                    "power24_dbm": values[5],
                    "util24_percent": values[6].split("(")[0],
                    "noise24_dbm": values[7].split("(")[0],
                }
        aps.append({
            "name": name,
            "ip": ip,
            "clients": match.group("clients"),
            "model": match.group("model"),
            "raw": tail,
            **radio,
        })
    return aps


def parse_client_table(output: str) -> list[dict[str, Any]]:
    """Parse rows from Instant `show clients`, tolerating wrapped CLI tables."""
    import re

    lines = _clean_cli(output).splitlines()
    mac_re = re.compile(r"(?i)(?:[0-9a-f]{2}:){5}[0-9a-f]{2}")
    channel_re = re.compile(r"^(?:[1-9]|1[0-4]|149E?|153E?|157E?|161E?|165E?|36E?|40E?|44E?|48E?|52E?|56E?|60E?|64E?|100E?|104E?|108E?|112E?|116E?|120E?|124E?|128E?|132E?|136E?|140E?)$")
    clients: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        mac_match = mac_re.search(line)
        if not mac_match:
            continue
        ip_match = re.search(r"(?:\d{1,3}\.){3}\d{1,3}", line)
        if not ip_match:
            continue
        window = " ".join(lines[index : min(index + 3, len(lines))])
        tokens = window.split()
        try:
            mac_i = next(i for i, token in enumerate(tokens) if mac_re.fullmatch(token))
            ap_i = next(
                i for i in range(mac_i + 3, len(tokens) - 6)
                if tokens[i] not in ("--", "-") and channel_re.fullmatch(tokens[i + 1])
            )
        except (StopIteration, ValueError):
            continue
        # Name is the first field and IP follows it. Spaces in names are uncommon in Instant client output.
        ip_index = next((i for i, token in enumerate(tokens) if token == ip_match.group(0)), None)
        if ip_index is None:
            continue
        signal = _numeric(tokens[ap_i + 5]) if len(tokens) > ap_i + 5 else None
        rate = _numeric(tokens[ap_i + 6]) if len(tokens) > ap_i + 6 else None
        clients.append(
            {
                "name": tokens[0],
                "ip": ip_match.group(0),
                "mac": tokens[mac_i],
                "ap": tokens[ap_i],
                "channel": tokens[ap_i + 1],
                "signal": signal,
                "rate": rate,
            }
        )
    # Avoid duplicates if a wrapped row was considered from more than one line.
    unique: dict[str, dict[str, Any]] = {str(row["mac"]).lower(): row for row in clients}
    return list(unique.values())


def _numeric(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None
