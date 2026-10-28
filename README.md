# Aruba Radio Tuner

A Home Assistant custom integration and Lovelace card for controlled Aruba Instant 2.4 GHz power/channel comparisons. It uses the Instant REST API over the local network; it does not use SSH or Aruba Central.

API reference: [Instant AOS-8.12 REST API Guide](https://arubanetworking.hpe.com/techdocs/Aruba-Instant-8.x-Books/812/Aruba-Instant-8.12.0.0-REST-API-Guide.pdf). The project uses the guide's `show aps`, `show clients`, and `/rest/channel` endpoints.

## Scope and behavior

- Select one AP from the Instant cluster. All tuning calls target that AP's 2.4 GHz `g-channel`; the integration does not write 5 GHz radio settings.
- Run a power sweep, a channel comparison, or a combined channel × power sweep. Power start, maximum, step, channels, and client recovery timeout are configurable for each run. Defaults are 8–13 dBm in 2 dB steps, channels 1/6/11, and 120 seconds.
- Each condition is applied through `/rest/channel`, then the integration polls `show aps` and `show clients` while waiting for client recovery. Results include the observed client set and available channel, power, utilization, noise, signal, and PHY rate values.
- A normal run restores the starting 2.4 GHz channel/power after collecting results. The card shows the recommendation and waits for **Apply recommendation**.
- A scheduled run starts unattended and applies the recommended 2.4 GHz setting when at least 75% of its baseline clients return. If that recovery threshold is not reached, it restores the starting setting and marks the result for review. Results are stored in Home Assistant and shown in the card's **Results** view. Schedules and results survive Home Assistant restarts; an interrupted run attempts a restore before resuming a scheduled test.

The recommendation score prioritizes client return, then the clients' median PHY rate and signal quality, then lower utilization, noise floor, and transmit power. It is a comparison aid based on the samples received from the AP, not a guarantee of optimal RF performance.

## Install

On Home Assistant releases before 2026.11, add `https://github.com/duarte-hub/aruba-radio-tuner` in HACS as a **custom integration**. Install **Aruba Radio Tuner** and restart Home Assistant. The integration also serves its card locally, so there is no separate frontend repository or external JavaScript host.

Home Assistant's Marketplace migration changes this path: starting with 2026.11, Home Assistant blocks custom integration installation through HACS. For 2026.11 and later, install from the Home Assistant Marketplace when available, or copy `custom_components/aruba_radio_tuner` into the HA `custom_components` directory and restart. The same local card resource works in either case. HACS and the Marketplace require a public GitHub repository; the integration manifest currently has owner/repository placeholders to fill in before publishing.

In Home Assistant, go to **Settings → Devices & services → Add integration → Aruba Radio Tuner**. Enter the Instant cluster IP, API username/password, and whether to validate the AP certificate. Aruba's default self-signed certificate typically requires certificate validation to be off unless you have installed a trusted certificate.

The Instant REST API must be enabled on the conductor AP (`allow-rest-api` in Instant CLI). This integration never enables it automatically. Setup performs an API login and `show version` check; it does not change radio settings. The tested cluster reports InstantOS 8.13.3.1_97206 LSR.

Add the card resource once under **Settings → Dashboards → Resources**:

```text
/api/aruba_radio_tuner/static/aruba-radio-tuner-card.js
```

Then add a Manual card to a dashboard:

```yaml
type: custom:aruba-radio-tuner
entity: sensor.aruba_radio_tuner
```

If the config-entry name differs from the default, use the entity ID shown under **Settings → Devices & services → Aruba Radio Tuner**.

## Testing limits

The unauthenticated REST endpoint check returned the expected missing-session response. Full login, command output parsing, and radio writes still need confirmation in the target Home Assistant instance with its API credentials. The integration deliberately does not claim that an AP honors a requested power: it records the live value reported by `show aps` after each change.

## Project status

This is an initial implementation. Authenticated API behavior still needs validation against the target AP before relying on scheduled automatic runs.
