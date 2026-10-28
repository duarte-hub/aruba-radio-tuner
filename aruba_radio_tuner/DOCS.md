# Aruba Radio Tuner — documentation

## Requirements

- Home Assistant OS with the Supervisor Apps feature.
- An Aruba Instant AOS-8 cluster with REST API enabled on its conductor AP. REST API access is on HTTPS port 4343.
- A management account accepted by the Instant API. Use a write-capable local management account; read-only accounts cannot tune radio settings.

## Configure the app

In **Settings → Apps → Aruba Radio Tuner → Configuration**, set:

| Option | Description |
|---|---|
| Cluster IP | The conductor/cluster IP, default `192.168.0.25`. |
| API username | Instant management username. |
| API password | Instant management password. Stored in the Home Assistant app options and never included in result data. |
| Validate certificate | Enable only when the AP certificate chains to a certificate authority trusted by the app container. Defaults off for the typical self-signed AP certificate. |

Save the options, then restart the app so it reloads the credentials. Open **Open Web UI** or enable **Show in sidebar** on the app page.

## Run a comparison

Choose an AP and one of three modes:

- **Power sweep:** tests configured power values on that AP's currently configured 2.4 GHz channel.
- **Channel comparison:** tests the configured channels at one fixed power.
- **Combined:** tests every configured power on every configured channel.

Defaults are start 8 dBm, maximum 13 dBm, 2 dB increments, channels 1/6/11, fixed channel-test power 13 dBm, and 120 seconds of recovery. The app polls client state during the recovery interval and records a sample when at least 75% of baseline 2.4 GHz clients return or the timeout expires. A sweep is limited to 120 conditions.

The AP may enforce a lower power than requested. Each sample records the requested and reported live power/channel alongside observed clients, utilization, and noise. A `show aps` inventory parse that lacks the live radio fields prevents a run from changing anything.

Instant marks the conductor row in `show aps` with `*` after its IP address (for example, `192.168.0.4*`). The app strips that marker while parsing so the conductor remains selectable as a normal AP address.

### Manual run

Select **Run comparison**. The app captures the starting settings, runs the conditions, then restores the starting channel and power. It shows the recommendation and waits for **Apply recommendation**. Applying a recommendation is a separate deliberate action.

### Scheduled run

Select **Schedule**, choose a start time in the browser device's local time zone, and save. The app converts and persists the schedule as UTC, so it remains correct across restarts and daylight-saving changes. A scheduled run applies its recommended channel/power only when at least 75% of the baseline clients return. If the threshold is not met, it restores the original settings and flags the result for review.

## Recovery and failure behavior

Before the first test change, the app persists the starting 2.4 GHz channel and power. If a run is cancelled or an API error occurs, it attempts to restore those settings. If the app is stopped during a run, the saved recovery state is restored when it starts again. If restoration fails, the UI flags the failure and the operator should check the AP immediately.

The app never changes the selected AP's 5 GHz radio, does not use SSH or Aruba Central, and does not enable the REST API automatically. Use the results page to review or delete saved comparisons and to cancel pending schedules.

## Data and access

The API credential is read from the Supervisor-managed app options. The app stores schedules, comparison results, and any in-progress recovery snapshot under `/data/state.json`. The web UI is available through Home Assistant Ingress on port 8099; the app only accepts requests forwarded by Home Assistant's ingress proxy. No direct host port or separate web password is used.

## API implementation note

The REST login payload and supported commands follow HPE's [Instant AOS-8.12 REST API Guide](https://arubanetworking.hpe.com/techdocs/Aruba-Instant-8.x-Books/812/Aruba-Instant-8.12.0.0-REST-API-Guide.pdf). The user's cluster reports InstantOS `8.13.3.1_97206 LSR`; authenticated behavior and radio writes still need confirmation on the target cluster before relying on unattended schedules.
