# Quickshell integration

Install the maintained component into an existing Quickshell configuration:

```bash
pi-beacon install-quickshell YOUR_CONFIG_NAME
```

Then instantiate `PiBeaconPanel` from that configuration. The component is self-contained and exposes its palette, font, placement, refresh interval, recent-session limit, and section visibility as public properties.

```qml
PiBeaconPanel {
    fontFamily: "JetBrainsMono Nerd Font"
    panelColor: "#ee1e1e2e"
    surfaceColor: "#4d414558"
    accentColor: "#9580ff"
    topMargin: 78
    rightMargin: 12
    recentLimit: 3
    showCost: true
    showContext: true
    showRecent: true
}
```

Instantiate the service menu in the same configuration. Waybar opens it on right-click.

```qml
PiBeaconServiceMenu {
    fontFamily: "JetBrainsMono Nerd Font"
    topMargin: 46
    rightMargin: 12
}
```

`Quit Pi Beacon` stops the service for the current login session without disabling autostart. The menu remains available through Waybar in its offline state, so the user can start the service again. `Disable at login` is a separate action.

## Themes

`PiBeaconTheme.qml` contains the calm Zen default. Pass any compatible QML object through the public `theme` property to replace it without touching the driver.

```qml
PiBeaconTheme {
    id: warmTheme
    name: "Warm"
    panel: "#181818"
    surface: "#272727"
    accent: "#f0b56b"
    success: "#8ee89a"
}

PiBeaconPanel {
    theme: warmTheme
    panelWidth: 520
    panelRadius: 24
    contentPadding: 16
    contentGap: 12
}
```

The installed QML is readable source. Every section can be removed or reshaped. It keeps one `pi-beacon-stream --format snapshot` process open while the panel is visible and consumes versioned SSE updates from the private local service.

The component registers an IPC target:

```bash
qs -c YOUR_CONFIG_NAME ipc call piBeacon toggle
qs -c YOUR_CONFIG_NAME ipc call piBeacon close
qs -c YOUR_CONFIG_NAME ipc call piBeacon refresh
qs -c YOUR_CONFIG_NAME ipc call piBeaconServiceMenu toggle
qs -c YOUR_CONFIG_NAME ipc call piBeaconServiceMenu close
```

Set `PI_BEACON_STREAM_EXECUTABLE` if `pi-beacon-stream` is not at `~/.local/bin/pi-beacon-stream`. The `refreshInterval` property controls reconnect delay after an unexpected subscriber exit; service cadence is configured under `[service]`.
