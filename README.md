# Pi Beacon

[![CI](https://github.com/andres-ortizl/pi-beacon/actions/workflows/ci.yml/badge.svg)](https://github.com/andres-ortizl/pi-beacon/actions/workflows/ci.yml)
[![Codecov](https://codecov.io/gh/andres-ortizl/pi-beacon/graph/badge.svg)](https://codecov.io/gh/andres-ortizl/pi-beacon)
[![PyPI](https://img.shields.io/pypi/v/pi-beacon.svg)](https://pypi.org/project/pi-beacon/)
[![npm](https://img.shields.io/npm/v/pi-beacon.svg)](https://www.npmjs.com/package/pi-beacon)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Live Pi session and subagent observability for Linux desktops.

**[Website](https://andres-ortizl.github.io/pi-beacon/)** · **[Documentation](docs/)** · **[Install](#installation)**

Pi Beacon turns Pi's in-process state, session logs, and `pi-subagents` lifecycle artifacts into one versioned local snapshot. It ships a Waybar indicator and a configurable Quickshell dashboard.

Inspired by the local-first analytics approach in [phun333/pi-infobar](https://github.com/phun333/pi-infobar), with a Linux-first live bridge instead of a native macOS or Windows application.

## Features

- Every open Pi session, including `idle`, `running`, and `waiting` states.
- Current model, thinking level, active tool, prompt, context usage, tokens, and cost.
- Active `pi-subagents` children and attention state.
- Configurable Freedesktop notifications for completed work, waiting prompts, and errors.
- Optional bundled completion sound with `pw-play`, `paplay`, or `canberra-gtk-play` fallback.
- Today's sessions, messages, tokens, and reported cost.
- Self-healing async SQLite cache with Alembic migrations and bounded retention.
- Versioned HTTP, JSON, JSON Schema, and SSE contracts for custom frontends.
- Persistent Granian and uvloop service on a private Unix socket.
- Configurable Quickshell panel and Waybar module with no periodic process spawning.
- Local-only operation. No telemetry or TCP listener.

## Installation

Run the interactive installer:

```bash
curl -fsSL https://raw.githubusercontent.com/andres-ortizl/pi-beacon/v1.0.0/install.sh | sh
```

Choose Install/Update or Uninstall. The script adds `uv` when required, installs the backend and version-pinned Pi extension, configures the user service, detects Quickshell, and asks which local data to preserve. It never uses `sudo`. Restart Pi or run `/reload` after installation.

For the safer inspect-then-run path:

```bash
curl -fsSLo /tmp/pi-beacon-install.sh \
  https://raw.githubusercontent.com/andres-ortizl/pi-beacon/v1.0.0/install.sh
less /tmp/pi-beacon-install.sh
sh /tmp/pi-beacon-install.sh
```

Use `sh install.sh --help` for non-interactive automation flags such as `--yes`, `--version`, `--quickshell`, and `--purge`.
The service uses one Granian ASGI worker with uvloop. It listens only on the private Unix socket `$XDG_RUNTIME_DIR/pi-beacon/api.sock`.

### Quickshell

Copy the maintained component into an existing Quickshell configuration:

```bash
pi-beacon install-quickshell YOUR_CONFIG_NAME
```

Instantiate it from that configuration:

```qml
PiBeaconPanel {
    fontFamily: "JetBrainsMono Nerd Font"
    panelColor: "#ee1e1e2e"
    accentColor: "#9580ff"
    topMargin: 78
    rightMargin: 12
}
```

```qml
PiBeaconServiceMenu {
    fontFamily: "JetBrainsMono Nerd Font"
    topMargin: 46
    rightMargin: 12
}
```

The components register dashboard and service-menu IPC targets:

```bash
qs -c YOUR_CONFIG_NAME ipc call piBeacon toggle
qs -c YOUR_CONFIG_NAME ipc call piBeaconServiceMenu toggle
```

### Waybar

Merge `integrations/waybar/config.jsonc` into your Waybar configuration and include `integrations/waybar/style.css` from your theme. The essential module is:

```jsonc
"custom/pi-beacon": {
  "exec": "$HOME/.local/bin/pi-beacon-stream --format waybar",
  "return-type": "json",
  "tooltip": true,
  "on-click": "qs -c YOUR_QUICKSHELL_CONFIG ipc call piBeacon toggle",
  "on-click-right": "qs -c YOUR_QUICKSHELL_CONFIG ipc call piBeaconServiceMenu toggle"
}
```

Right-click opens Start, Restart, `Quit Pi Beacon`, and `Disable at login`. Quit stops the service now but preserves next-login autostart. The Waybar module remains available in an offline state so Pi Beacon can be started again.

## CLI

```bash
pi-beacon serve                  # Granian + uvloop API on the private Unix socket
pi-beacon-stream                 # Lightweight persistent SSE subscriber
pi-beacon subscribe              # Equivalent compatibility command
pi-beacon-stream --format waybar
pi-beacon waybar                 # One-shot compatibility payload
pi-beacon snapshot --pretty      # One-shot compatibility snapshot
pi-beacon doctor                 # Resolved paths and input availability
pi-beacon database-upgrade       # Apply pending Alembic migrations
pi-beacon notify settled --project demo --duration 12
pi-beacon init-config            # Install ~/.config/pi-beacon/config.toml
pi-beacon install-systemd        # Install, but do not enable, the user unit
pi-beacon install-quickshell NAME
```

## Configuration

Run `pi-beacon init-config`, or copy `config.example.toml`:

```toml
schema_version = 1
recent_session_limit = 5

[service]
runtime_interval_seconds = 2
history_interval_seconds = 60
history_enabled = true
history_retention_days = 7
watch_debounce_ms = 250

[display]
show_cost = true
show_context = true
show_recent_sessions = true

[notifications]
enabled = true
settled = true
waiting = true
errors = true
minimum_duration_seconds = 10
sound = false
# quiet_hours_start = "22:00"
# quiet_hours_end = "08:00"

[paths]
# sessions_dir = "~/.pi/agent/sessions"
# runtime_dir = "/run/user/1000/pi-beacon"
# database = "~/.cache/pi-beacon/sessions.sqlite3"
# socket = "/run/user/1000/pi-beacon/api.sock"
# subagent_runs_dir = "/tmp/pi-subagents-uid-1000/async-subagent-runs"
```

Environment overrides use the `PI_BEACON_` prefix and `__` for nesting. Example:

```bash
export PI_BEACON_PATHS__RUNTIME_DIR=/run/user/$UID/pi-beacon
```

The service owns data refresh cadence. The Quickshell component exposes colors, font, placement, reconnect interval, recent-session limit, and section visibility as QML properties.

## Philosophy

Pi Beacon provides the driver, the public contract and one calm Zen default. It does not own your desktop.

- The backend has no Waybar or Quickshell dependency.
- `snapshot v1` is the stable frontend boundary.
- The maintained QML is readable source, not generated output.
- `PiBeaconTheme.qml` is replaceable through the panel's public `theme` property.
- Colors, typography, dimensions, placement, refresh cadence and visible sections are public properties.
- Other frontends can consume the same JSON without importing QML.

## Architecture

```text
Pi extension ──atomic live events──▶ runtime status files ──┐
pi-subagents lifecycle artifacts ───────────────────────────┤
Pi JSONL ──validated incremental reads──▶ async SQLite cache ┤
                                                           ▼
                                           persistent collector
                                           FastAPI + Granian + uvloop
                                                           │
                                         HTTP/JSON + SSE over Unix socket
                                                           │
                                           Quickshell / Waybar / custom UI
```

The extension publishes current-session values directly from `sessionManager`, `getContextUsage()`, `getEntries()`, and Pi lifecycle events. It does not reparse the active session to obtain values already held by Pi.

The persistent collector watches the live bridge and Pi session files, coalesces filesystem events, refreshes live and historical data on separate cadences, and publishes monotonic snapshot revisions. A periodic reconciliation recovers from missed filesystem events.

SQLModel defines the cache tables. SQLAlchemy async and `aiosqlite` provide non-blocking access, while Alembic controls schema migrations. The cache validates inode and cursor fingerprints, recomputes rewritten sessions, removes deleted current-day sessions, and applies bounded retention.

See [docs/api-v1.md](docs/api-v1.md) for the local HTTP and SSE protocol. See [docs/bridge-v1.md](docs/bridge-v1.md) for the producer and snapshot contracts. See [docs/subagent-adapter-v1.md](docs/subagent-adapter-v1.md) for the optional `pi-subagents` compatibility boundary.

## Development

```bash
uv sync --locked --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv run zizmor .github/workflows
npm run format:check
npm run lint
npm run typecheck
uv build
```

Tests enforce at least 85% Python coverage. Biome owns JavaScript, TypeScript, JSON, and CSS formatting and linting; Prettier remains only for HTML and workflow YAML. Zizmor audits pinned GitHub Actions. GitHub Actions runs the same quality, security, test, and build gates.

## Privacy

Pi Beacon reads local Pi session files and local runtime state. It does not upload data or open a TCP listener. The API uses a mode `0600` Unix socket inside a mode `0700` user runtime directory. Live bridge files use the same private runtime root and are removed during normal Pi shutdown. The SQLite cache stores bounded operational metadata, not transcript bodies.

## License

MIT
