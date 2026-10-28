# Aruba Radio Tuner

Standalone browser-based Aruba Instant 2.4 GHz radio comparison app for Home Assistant OS.

Configure the Instant conductor IP and management API credentials in the app's **Configuration** tab, start the app, then select **Open Web UI**. The interface is served through Home Assistant Ingress, so Home Assistant handles browser authentication and no app port is exposed on the host.

The app can run power sweeps, channel comparisons, or combined tests. Manual runs restore the starting radio settings and wait for you to apply a recommendation. Scheduled runs can apply automatically when client recovery is sufficient. All tests target only the selected AP's 2.4 GHz radio.

Read [`DOCS.md`](DOCS.md) for full setup, behavior, and safety details.
