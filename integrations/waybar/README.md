# Waybar integration

Merge [`config.jsonc`](config.jsonc) into the active Waybar configuration and copy the relevant rules from [`style.css`](style.css).

The module starts one persistent `pi-beacon-stream --format waybar` process. The subscriber consumes SSE revisions from the private local service and writes one Waybar JSON line per update. It shows:

- `π N` for open main sessions
- `+N` while subagents are active
- muted color when all sessions are idle
- active color while work runs
- attention color while Pi waits for input or a child needs attention

Hover for a compact live summary. Configure `on-click` to call the `piBeacon` Quickshell IPC target.
