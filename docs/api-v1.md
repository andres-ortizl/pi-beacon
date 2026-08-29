# Pi Beacon local API v1

Pi Beacon exposes read-only HTTP/1.1 and Server-Sent Events over a private Unix domain socket. It does not open a TCP listener.

## Transport

The default socket is:

```text
$XDG_RUNTIME_DIR/pi-beacon/api.sock
```

When `XDG_RUNTIME_DIR` is unavailable, both the Pi extension and backend use:

```text
$TMPDIR/pi-runtime-<uid>/pi-beacon/api.sock
```

The runtime directory uses mode `0700`. Granian creates the socket with mode `0600`. The service runs one ASGI worker with uvloop so the collector, async database engine, and SSE revisions have one owner.

Override the socket with `PI_BEACON_PATHS__SOCKET` or `[paths].socket`.

## Lifecycle

Install the bundled systemd user unit:

```bash
pi-beacon install-systemd
systemctl --user daemon-reload
systemctl --user enable --now pi-beacon.service
```

The installer writes the unit but does not enable or start it. systemd owns restart policy and graceful termination.

At startup the service:

1. applies Alembic migrations;
2. opens SQLite through SQLAlchemy async and `aiosqlite`;
3. performs one runtime and historical reconciliation;
4. creates revision `1`;
5. starts filesystem watchers and periodic recovery tasks;
6. begins accepting requests.

Startup fails if the initial state cannot be produced. Later refreshes publish atomically, so readers either receive the previous complete snapshot or the next complete snapshot.

## Routes

All data routes share the `/v1` router prefix.

| Route | Result |
| --- | --- |
| `GET /healthz` | Process readiness and current revision |
| `GET /v1/snapshot` | Complete `DashboardSnapshot` |
| `GET /v1/runtime` | Current `RuntimeStatus` |
| `GET /v1/waybar` | Current Waybar JSON payload |
| `GET /v1/events` | Persistent SSE snapshot stream |
| `GET /v1/events/waybar` | Persistent pre-rendered Waybar SSE stream |
| `GET /openapi.json` | Generated OpenAPI contract |

Every `/v1` response includes `X-Pi-Beacon-Revision`. One ASGI middleware adds the header, including to streaming responses.

The Pydantic models and committed JSON Schemas remain the canonical payload definitions. FastAPI generates OpenAPI from the same models.

## Server-Sent Events

`GET /v1/events` emits complete snapshots. `GET /v1/events/waybar` uses the same revision stream with `event: waybar` and an already rendered `WaybarPayload`:

```text
id: 42
event: snapshot
retry: 1000
data: {"version":1,"generatedAt":"...","runtime":{},"today":{}}

```

Clients should:

- process only `snapshot` events;
- retain the latest event ID;
- send `Last-Event-ID` after reconnecting;
- replace their complete local snapshot rather than patching fields;
- ignore comment heartbeats;
- ignore unknown additive JSON fields.

Event IDs are monotonic for one service process. If a client reconnects after a service restart with an ID greater than the new process revision, the service immediately sends its current snapshot.

The bundled persistent client handles framing and reconnect backoff:

```bash
pi-beacon-stream --format snapshot
pi-beacon-stream --format waybar
```

Use `--once` for diagnostics.

## Collection guarantees

Normal updates are triggered by filesystem notifications. Timed tasks provide recovery and time-dependent refreshes:

- runtime state reconciles every two seconds by default;
- historical state reconciles every 60 seconds by default;
- watcher events are coalesced before refresh;
- only one historical refresh writes SQLite at a time;
- SQLite uses WAL, foreign keys, and a five-second busy timeout;
- incomplete trailing JSONL lines are not committed;
- inode and cursor fingerprints detect rewrites;
- rewritten files are recomputed transactionally;
- missing current-day source files are removed from the cache;
- old cache rows are removed after the configured retention period.

Configure these values under `[service]`. Set `history_enabled = false` to disable session traversal and historical database initialization while retaining live observability.

## Security boundary

The Unix socket relies on filesystem permissions instead of application tokens. Any process running as the same user can read the API, just as it can read that user's Pi session files.

Do not expose the socket through a TCP proxy without adding authentication, origin policy, request limits, and a separate threat review. Snapshot fields can include private paths, prompt-derived titles, and a truncated current prompt.
