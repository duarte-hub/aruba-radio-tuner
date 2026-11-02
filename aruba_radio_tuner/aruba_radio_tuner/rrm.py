"""AP-to-AP signal analysis and 2.4 GHz radio tweak suggestions.

Each AP reports how loudly it hears the other APs in the cluster. Those readings
describe cell overlap: too loud means cells overlap heavily (sticky clients, co-channel
interference), too quiet means a coverage gap that clients can't roam across.
"""

from __future__ import annotations

import math
from typing import Any

# Strongest neighbour signal we want a given AP to produce, in dBm.
LOUD_DBM = -60       # above this, cells overlap too much
TARGET_DBM = -67     # aim here when turning an AP down
QUIET_DBM = -78      # below this, roaming handoff is weak
RAISE_TARGET_DBM = -72
COCHANNEL_DBM = -80  # same channel and louder than this is interference
CHANNELS = (1, 6, 11)
DEFAULT_NOISE_DBM = -95.0
MAX_STEP_DB = 6


def build_links(
    aps: list[dict[str, Any]],
    bss_map: dict[str, dict[str, Any]],
    heard_by_ap: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Turn raw per-AP neighbour lists into directed links `listener <- speaker`."""
    noise = {ap["name"]: _float(ap.get("noise24_dbm")) for ap in aps}
    links: dict[tuple[str, str], dict[str, Any]] = {}
    for listener, rows in heard_by_ap.items():
        for row in rows:
            speaker = bss_map.get(row["bssid"])
            if not speaker or speaker["ap_name"] == listener:
                continue
            signal = float(row["signal"])
            if row.get("unit") == "snr":
                signal += noise.get(listener) if noise.get(listener) is not None else DEFAULT_NOISE_DBM
            key = (listener, speaker["ap_name"])
            # An AP can advertise several BSSIDs; keep the loudest.
            if key not in links or signal > links[key]["signal_dbm"]:
                links[key] = {
                    "listener": listener,
                    "speaker": speaker["ap_name"],
                    "signal_dbm": round(signal),
                    "channel": row.get("channel") or speaker.get("channel"),
                }
    return sorted(links.values(), key=lambda link: (link["listener"], link["speaker"]))


def suggest(
    aps: list[dict[str, Any]],
    links: list[dict[str, Any]],
    *,
    min_power_dbm: float = 6,
    max_power_dbm: float = 13,
) -> list[dict[str, Any]]:
    """Return at most one suggested tweak per AP, with the reasons behind it."""
    by_name = {ap["name"]: ap for ap in aps}
    footprint: dict[str, list[dict[str, Any]]] = {name: [] for name in by_name}
    for link in links:
        if link["speaker"] in footprint:
            footprint[link["speaker"]].append(link)

    suggestions = []
    for name, ap in by_name.items():
        channel = _int(ap.get("channel24"))
        power = _float(ap.get("power24_dbm"))
        heard = footprint[name]
        reasons: list[str] = []
        new_power, new_channel = power, channel

        if not heard:
            suggestions.append(_entry(ap, channel, power, None, None, [
                "No other AP reported hearing this one. Not enough data to suggest a change."
            ], heard))
            continue

        loudest = max(heard, key=lambda link: link["signal_dbm"])
        if power is not None and loudest["signal_dbm"] > LOUD_DBM:
            # The neighbour is usually turned down too, so each side takes half the cut.
            drop = min(MAX_STEP_DB, math.ceil((loudest["signal_dbm"] - TARGET_DBM) / 2))
            candidate = max(min_power_dbm, power - drop)
            if candidate < power:
                new_power = candidate
                reasons.append(
                    f"{loudest['listener']} hears it at {loudest['signal_dbm']} dBm, which is very "
                    f"loud. Overlapping cells keep clients stuck on a distant AP. Reduce by "
                    f"{power - new_power:g} dB."
                )
        elif power is not None and loudest["signal_dbm"] < QUIET_DBM:
            gain = min(MAX_STEP_DB, math.ceil(RAISE_TARGET_DBM - loudest["signal_dbm"]))
            candidate = min(max_power_dbm, power + gain)
            if candidate > power:
                new_power = candidate
                reasons.append(
                    f"Its best neighbour ({loudest['listener']}) only hears it at "
                    f"{loudest['signal_dbm']} dBm. Clients may lose signal before they can roam. "
                    f"Raise by {new_power - power:g} dB."
                )
            else:
                reasons.append(
                    f"Its best neighbour ({loudest['listener']}) only hears it at "
                    f"{loudest['signal_dbm']} dBm, but power is already at or above the "
                    f"{max_power_dbm:g} dBm limit, so no change is suggested. Check the reading "
                    f"or the AP placement."
                )

        # Co-channel: another AP on the same channel is audible enough to share airtime.
        cochannel = [
            link for link in heard
            if channel is not None
            and _int(by_name.get(link["listener"], {}).get("channel24")) == channel
            and link["signal_dbm"] > COCHANNEL_DBM
        ]
        if cochannel and _should_move(name, cochannel, by_name, footprint):
            new_channel = _best_channel(name, channel, links, by_name)
            if new_channel != channel:
                names = ", ".join(sorted({link["listener"] for link in cochannel}))
                reasons.append(
                    f"Shares channel {channel} with {names} at a level that causes "
                    f"interference. Move to channel {new_channel}."
                )

        if not reasons:
            reasons.append(
                f"Strongest neighbour signal is {loudest['signal_dbm']} dBm, a healthy overlap "
                f"for roaming. No change suggested."
            )
        suggestions.append(_entry(ap, channel, power, new_channel, new_power, reasons, heard))
    return suggestions


def _entry(ap, channel, power, new_channel, new_power, reasons, heard):
    changed = (new_channel is not None and new_channel != channel) or (
        new_power is not None and new_power != power
    )
    return {
        "ap_name": ap["name"],
        "ap_ip": ap["ip"],
        "channel": channel,
        "power_dbm": power,
        "suggested_channel": new_channel if changed else channel,
        "suggested_power_dbm": new_power if changed else power,
        "change": changed,
        "reasons": reasons,
        "heard_by": [
            {"ap": link["listener"], "signal_dbm": link["signal_dbm"]}
            for link in sorted(heard, key=lambda link: -link["signal_dbm"])
        ],
    }


def _should_move(name, cochannel, by_name, footprint) -> bool:
    """Of two interfering APs only one should change; pick the more crowded one."""
    mine = len(cochannel)
    for link in cochannel:
        other = link["listener"]
        theirs = sum(
            1 for item in footprint.get(other, [])
            if _int(by_name.get(item["listener"], {}).get("channel24"))
            == _int(by_name.get(other, {}).get("channel24"))
            and item["signal_dbm"] > COCHANNEL_DBM
        )
        if theirs > mine or (theirs == mine and other < name):
            return False
    return True


def _best_channel(name, current, links, by_name) -> int:
    """Pick 1/6/11 with the least audible neighbour energy for this AP."""
    def energy(channel: int) -> float:
        total = 0.0
        for link in links:
            if name not in (link["listener"], link["speaker"]):
                continue
            other = link["speaker"] if link["listener"] == name else link["listener"]
            if _int(by_name.get(other, {}).get("channel24")) == channel:
                total += 10 ** (link["signal_dbm"] / 10)
        return total

    return min(CHANNELS, key=lambda channel: (energy(channel), channel != current))


def _float(value: Any) -> float | None:
    try:
        return float(str(value).replace(" dBm", "").replace("%", ""))
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None
