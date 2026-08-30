import QtQuick
import Quickshell

ShellRoot {
    PiBeaconPanel {
        // Every property is optional. Override these to match your shell.
        fontFamily: "JetBrainsMono Nerd Font"
        panelColor: "#ee1e1e2e"
        accentColor: "#9580ff"
        topMargin: 78
        rightMargin: 12
    }

    PiBeaconServiceMenu {
        fontFamily: "JetBrainsMono Nerd Font"
        panelColor: "#ee1e1e2e"
        accentColor: "#9580ff"
        topMargin: 46
        rightMargin: 12
    }
}
