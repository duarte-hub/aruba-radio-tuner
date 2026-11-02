"""Small client for the Aruba Instant REST API."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

_LOGGER = logging.getLogger(__name__)


_RETRY_STATUSES = (429, 502, 503, 504)
_RETRY_DELAYS = (2, 5, 10)


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
        # The Instant REST server is single-threaded and answers 503 when busy, so
        # issue one command at a time.
        self._cmd_lock = asyncio.Lock()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_err: Exception | None = None
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                async with self._session.request(
                    method,
                    f"{self.base_url}{path}",
                    params=params,
                    json=json,
                    ssl=self.verify_ssl,
                    timeout=ClientTimeout(total=20),
                ) as response:
                    if response.status in _RETRY_STATUSES and attempt < len(_RETRY_DELAYS):
                        last_err = ClientError(f"{response.status} {response.reason}")
                    else:
                        response.raise_for_status()
                        body = await response.json(content_type=None)
                        if not isinstance(body, dict):
                            raise ArubaApiError("Instant API returned an unexpected response")
                        return body
            except (ClientError, asyncio.TimeoutError, ValueError) as err:
                raise ArubaApiError(f"Instant API request failed: {err}") from err
            _LOGGER.debug("Instant API busy (%s); retrying in %ss", last_err, _RETRY_DELAYS[attempt])
            await asyncio.sleep(_RETRY_DELAYS[attempt])
        raise ArubaApiError(f"Instant API request failed: {last_err}")

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
        async with self._cmd_lock:
            return await self._async_show_locked(ap_ip, command)

    async def _async_show_locked(self, ap_ip: str, command: str) -> str:
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

    async def async_get_bss_table(self) -> str:
        """Cluster-wide BSSID table; maps radio BSSIDs back to member APs."""
        return await self.async_show(self.host, "show ap bss-table")

    async def async_get_monitor_list(self, ap_ip: str) -> str:
        """Raw list of nearby APs this AP hears, with signal strength."""
        return await self.async_show(ap_ip, "show ap monitor ap-list")

    async def async_set_24ghz(self, ap_ip: str, channel: int, power_dbm: float) -> None:
        """Set only the target AP's 2.4 GHz channel and transmit power."""
        async with self._cmd_lock:
            await self._async_set_24ghz_locked(ap_ip, channel, power_dbm)

    async def _async_set_24ghz_locked(self, ap_ip: str, channel: int, power_dbm: float) -> None:
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
        # Instant marks the conductor's address with a trailing '*'. Keep the
        # marker out of the stored IP so it remains a usable API target.
        r"^(?P<name>[^\s]+)\s+(?P<ip>(?:\d{1,3}\.){3}\d{1,3})\*?\s+"
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


_MAC_RE = None


def _mac_re():
    global _MAC_RE
    if _MAC_RE is None:
        import re

        _MAC_RE = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
    return _MAC_RE


def _split_cells(line: str) -> list[str]:
    import re

    return [cell.strip() for cell in re.split(r"\s{2,}", line.strip()) if cell.strip()]


def _find_column(header: list[str], *needles: str) -> int | None:
    for index, cell in enumerate(header):
        lowered = cell.lower()
        if any(needle in lowered for needle in needles):
            return index
    return None


def _table_rows(output: str, key: str) -> tuple[list[str], list[list[str]]]:
    """Return (header cells, data rows) for the table whose header contains `key`.

    Instant's CLI tables are separated by two or more spaces, so columns are
    identified by header name rather than fixed position. That tolerates firmware
    differences in column order and extra columns.
    """
    lines = _clean_cli(output).splitlines()
    for index, line in enumerate(lines):
        if key in line.lower() and not _mac_re().search(line):
            header = _split_cells(line)
            rows = []
            for row in lines[index + 1 :]:
                if set(row) <= set("- "):
                    continue
                rows.append(_split_cells(row))
            return header, rows
    return [], []


def parse_bss_table(output: str, known_ap_names: list[str]) -> dict[str, dict[str, Any]]:
    """Map lowercase radio BSSID -> {ap_name, channel} for cluster members."""
    header, rows = _table_rows(output, "bssid")
    names = {name.lower(): name for name in known_ap_names}
    ap_col = _find_column(header, "ap name")
    ch_col = _find_column(header, "ch")
    mapping: dict[str, dict[str, Any]] = {}
    for cells in rows:
        mac_match = _mac_re().search(" ".join(cells))
        if not mac_match:
            continue
        ap_name = None
        if ap_col is not None and ap_col < len(cells) and cells[ap_col].lower() in names:
            ap_name = names[cells[ap_col].lower()]
        if ap_name is None:
            # Header/columns unreliable: any cell (or word) matching a known AP name will do.
            for token in [part for cell in cells for part in (cell, *cell.split())]:
                if token.lower() in names:
                    ap_name = names[token.lower()]
                    break
        if ap_name is None:
            continue
        channel = None
        if ch_col is not None and ch_col < len(cells):
            channel = _numeric(cells[ch_col].split("/")[0].rstrip("EeSsPp+-"))
        mapping[mac_match.group(0).lower()] = {"ap_name": ap_name, "channel": channel}
    return mapping


def parse_monitor_list(output: str) -> list[dict[str, Any]]:
    """Parse nearby-AP rows: bssid, channel, and signal (dBm RSSI or dB SNR)."""
    header, rows = _table_rows(output, "bssid")
    sig_col = _find_column(header, "rssi", "snr", "signal")
    ch_col = _find_column(header, "chan", "ch")
    heard: list[dict[str, Any]] = []
    for cells in rows:
        mac_match = _mac_re().search(" ".join(cells))
        if not mac_match or sig_col is None or sig_col >= len(cells):
            continue
        raw = cells[sig_col].split("/")[0].split("(")[0]
        value = _numeric(raw.strip())
        if value is None:
            continue
        unit = "dbm" if value < 0 else "snr"
        if value > 0 and "rssi" in header[sig_col].lower():
            # Some firmware prints the dBm magnitude without the minus sign.
            value, unit = -value, "dbm"
        channel = None
        if ch_col is not None and ch_col < len(cells):
            channel = _numeric(cells[ch_col].split("/")[0].rstrip("EeSsPp+-"))
        heard.append({"bssid": mac_match.group(0).lower(), "channel": channel,
                      "signal": value, "unit": unit})
    return heard
