# Aruba Radio Tuner

A standalone Home Assistant app for comparing Aruba Instant 2.4 GHz radio settings. It runs as its own supervised container and provides a browser interface inside Home Assistant; no Lovelace card or Home Assistant integration setup is required.

## Install

This app requires Home Assistant OS with the Supervisor Apps feature. Add this repository under **Settings → Apps → Install app → ⋮ → Repositories**:

```text
https://github.com/duarte-hub/aruba-radio-tuner
```

Install **Aruba Radio Tuner**, then open its **Configuration** tab and enter the Instant conductor IP, management username, and password. The default cluster IP is `192.168.0.25`; API certificate validation defaults off for Aruba's usual self-signed certificate. Save the options and start or restart the app. Use **Open Web UI** in the app page (or enable **Show in sidebar**).

The app connects to InstantOS over HTTPS on port 4343. Enable the REST API on the conductor AP first. It uses the app's stored configuration and Home Assistant Ingress for browser authentication; the app does not expose a host port.

## What it does

- Power sweep, channel comparison, or combined channel × power sweep.
- Defaults: 8–13 dBm in 2 dB increments, channels 1/6/11, and a 120-second client-recovery timeout. All are configurable per test.
- Every radio write targets the chosen AP's 2.4 GHz radio only.
- Manual tests restore the original channel and power, then show a recommendation for you to apply.
- Scheduled tests run unattended and apply the recommendation only if at least 75% of the baseline clients return. Otherwise, the original setting is restored and the result is marked for review.
- Runs, schedules, and results persist in the app's `/data` folder and survive app restarts.

Recommendations compare observed baseline client return, median PHY rate and signal, channel utilization, noise, and requested transmit power. They are a comparison aid, not a guarantee of best RF performance. The app records the live channel and power reported by Instant after each request because the AP can clamp requested power.

See [`aruba_radio_tuner/DOCS.md`](aruba_radio_tuner/DOCS.md) for setup and operation details.

## Roaming (AP-to-AP signal)

The **Roaming** page scans what each AP hears from the others and shows a "who hears whom" grid in dBm, with suggested 2.4 GHz changes:

- A neighbour hears an AP louder than −60 dBm: reduce its power (overlap is too heavy, so clients stay on a distant AP).
- Its best neighbour hears it weaker than −78 dBm: raise power (a coverage gap clients cannot roam across).
- Two APs share a channel at an audible level: move one to the quietest of 1/6/11.

Scanning is read-only. Nothing changes until you press **Apply** on a suggestion. If your firmware's output can't be parsed, the page shows the raw output so the parser can be adjusted.

## Notes

- The Instant REST server answers `503` when busy, so the app sends commands one at a time and retries with a short back-off.
- Saved results include a channel × power heatmap, a per-condition chart and a score bar.
- The earlier HACS integration and Lovelace card have been removed; this app replaces them. If you installed the integration, remove it in HACS and delete its entry under **Settings → Devices & services**.
