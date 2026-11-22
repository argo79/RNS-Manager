# Sideband rangetest plugin

**English** | [Русский](README.ru.md) | [Italiano](README.it.md)

This command plugin adds the following LXMF command to Sideband:

```text
rangetest <interval> start
rangetest 0 stop
```

The plugin periodically requests telemetry from the peer that sent the
command. Sideband stores the replies in its database; on stop, the plugin
exports points with coordinates as GPX and KML.

## Installation

1. In Sideband open `Settings` → `Plugins`.
2. Enable **Enable Plugins**.
3. Enable **Enable Command Plugins**.
4. Select the repository's `sideband_plugins/` folder as **Plugins Directory**.

   Alternatively, copy `rangetest.py` to a dedicated directory, for example:

   ```bash
   mkdir -p ~/.config/sideband/plugins
   cp sideband_plugins/rangetest.py ~/.config/sideband/plugins/
   ```

   and select `~/.config/sideband/plugins`.

5. Restart Sideband.
6. Check the logs for:

   ```text
   Rangetest command plugin started
   Registered ... as handler for command "rangetest"
   ```

## Usage

From Sideband's **Send command or request** mode:

```text
rangetest 15 start
```

To stop:

```text
rangetest 0 stop
```

The interval is clamped between 5 and 3600 seconds.

Files are created in:

```text
~/.config/sideband/app_storage/rangetest/
```

with names:

```text
rangetest_<peer>_<timestamp>.gpx
rangetest_<peer>_<timestamp>.kml
```

## Permissions

To receive the `rangetest` command, Sideband must authorize the peer sending
it. Check the contact/trusted peer settings in Sideband.

To receive telemetry replies, the remote peer must allow telemetry requests:

- `allow_telemetry_requests = true` in RNS Messenger, or
- `allow_telemetry` enabled for that peer.

## Limitations

- Only points containing a valid location are exported.
- Sideband does not store RSSI/SNR/Q in the same reception record; the plugin
  exports available telemetry, not packet radio metrics.
- The plugin uses Sideband's `request_latest_telemetry()` and
  `peer_telemetry()` APIs, available in Sideband 1.5.0.
