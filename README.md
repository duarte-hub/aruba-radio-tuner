# Aruba Radio Tuner

A Home Assistant integration (installed through HACS) with a dashboard card for tuning the 2.4 GHz radio of Aruba Instant access points. It compares power and channel settings against real client behaviour, and it can analyse how loudly your APs hear each other so you can improve roaming.

## Install

1. In HACS, add this repository as a custom repository (type **Integration**): `https://github.com/duarte-hub/aruba-radio-tuner`.
2. Install **Aruba Radio Tuner** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → Aruba Radio Tuner**. Enter the Instant conductor IP, a write-capable management username and password. Certificate validation is off by default because APs normally use a self-signed certificate.
4. Add the card as a dashboard resource (**Settings → Dashboards → ⋮ → Resources**), type **JavaScript module**:

   ```text
   /api/aruba_radio_tuner/static/aruba-radio-tuner-card.js
   ```

5. Add a card to a dashboard:

   ```yaml
   type: custom:aruba-radio-tuner
   entity: sensor.<your_radio_tuner_sensor>
   ```

Requirements: an Aruba Instant (AOS-8) cluster with the REST API enabled on the conductor AP (HTTPS port 4343). Read-only accounts cannot change radio settings. After updating, hard-refresh the browser so it loads the new card.

## Radio tuning

Choose an AP and a mode:

- **Power sweep:** tests power values on the AP's current 2.4 GHz channel.
- **Channel comparison:** tests several channels at one fixed power.
- **Combined:** tests every power on every channel.

Defaults are 8–13 dBm in 2 dB steps, channels 1/6/11 and a 120-second client-recovery timeout; all are configurable per test, and a run is limited to 120 conditions. The tuner samples after at least 75% of the baseline 2.4 GHz clients return or the timeout expires. Each sample records the requested and live power and channel (the AP may clamp power), clients, utilization and noise.

- **Manual runs** restore the starting channel and power, then show a recommendation for you to apply.
- **Scheduled runs** apply the recommendation only if at least 75% of baseline clients return. Otherwise they restore the starting setting and mark the result for review.
- Results show a channel × power heatmap, a per-condition chart and a score bar.

Recommendations compare client return, median PHY rate and signal, utilization, noise and transmit power. They are a comparison aid, not a guarantee of best RF performance.

## Roaming (AP-to-AP signal)

The **Roaming** tab scans what each AP hears from the others and shows a "who hears whom" grid in dBm. It suggests a power or channel change per AP:

- A neighbour hears it louder than −60 dBm: reduce power (overlap is too heavy and clients stay on a distant AP).
- Its best neighbour hears it weaker than −78 dBm: raise power (a coverage gap clients cannot roam across).
- Two APs share a channel at an audible level: move one to the quietest of 1/6/11.

Scanning is read-only. Nothing changes until you press **Apply** on a suggestion. If the scan cannot parse your firmware's output, it shows the raw output so the parser can be adjusted.

## Services

| Service | Purpose |
|---|---|
| `aruba_radio_tuner.start_test` | Start a comparison run. |
| `aruba_radio_tuner.schedule_test` | Schedule a run for a later time. |
| `aruba_radio_tuner.apply_recommendation` | Apply a saved run's recommendation. |
| `aruba_radio_tuner.cancel_test` / `cancel_schedule` | Cancel a running test or a pending schedule. |
| `aruba_radio_tuner.delete_result` | Delete a saved result. |
| `aruba_radio_tuner.scan_neighbors` | Read AP-to-AP signal levels and build suggestions. |
| `aruba_radio_tuner.apply_rrm_suggestion` | Apply one AP's suggested channel and power. |

## Safety and recovery

- Every change targets one AP's 2.4 GHz radio. The 5 GHz radio is never touched; the integration does not use SSH or Aruba Central and does not enable the REST API for you.
- Before the first test change, the starting channel and power are saved. If a run is cancelled, fails, or Home Assistant restarts mid-run, the integration restores them. If restoration fails, the card shows an error: check the AP.
- Instant marks the conductor in `show aps` with `*` after its IP; the integration strips it so the conductor stays selectable.
- The Instant REST server answers `503` when busy, so the integration sends commands one at a time and retries with a short back-off.
- Schedules, results and recovery state are stored in Home Assistant's `.storage` and survive restarts. Credentials are never included in result data.

## API notes

The REST login payload and commands follow HPE's [Instant AOS-8.12 REST API Guide](https://arubanetworking.hpe.com/techdocs/Aruba-Instant-8.x-Books/812/Aruba-Instant-8.12.0.0-REST-API-Guide.pdf). Confirm radio writes on your own cluster before relying on unattended schedules.

## Moving from the old standalone app

Earlier versions also shipped a standalone Home Assistant app. It has been removed. Uninstall it in **Settings → Apps**, remove this repository from the app repositories list, and use the integration instead. The app's saved results do not carry over.
