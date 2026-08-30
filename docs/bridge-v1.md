# Pi Beacon bridge v1

Pi Beacon exposes two local, versioned read surfaces.

## Live session bridge

Each Pi process writes one atomic JSON file:

```text
$XDG_RUNTIME_DIR/pi-beacon/sessions/<pid>.json
```

The Pi extension writes through a temporary file and `rename`, so readers never observe a partial document. Files use mode `0600`; the containing directory uses mode `0700`. The predictable fallback path validates ownership and rejects symbolic links before use.

Representative payload:

```json
{
  "version": 1,
  "pid": 1234,
  "parentPid": 1000,
  "instanceId": "0f46cb86-...",
  "processStartTime": "48129942",
  "sessionId": "01abc...",
  "sessionFile": "/home/user/.pi/agent/sessions/project/session.jsonl",
  "sessionName": "dashboard work",
  "displayName": "Build a polished release dashboard",
  "titleSource": "prompt",
  "cwd": "/home/user/code/project",
  "project": "project",
  "state": "running",
  "detail": "Tool: edit",
  "prompt": "Implement the status bridge",
  "model": "openai/gpt-5.6-sol",
  "thinking": "xhigh",
  "usage": {
    "input": 120,
    "output": 450,
    "cacheRead": 84000,
    "cacheWrite": 0,
    "cost": 0.42,
    "subagentCost": 0,
    "totalTokens": 84570
  },
  "context": {
    "tokens": 510000,
    "contextWindow": 1050000,
    "percent": 48.57
  },
  "startedAt": 1788000000000,
  "lastMessageAt": 1788000009000,
  "updatedAt": 1788000010000
}
```

Consumers must ignore unknown fields. Supported states are `open`, `idle`, `running`, and `waiting`. `instanceId` plus the Linux process start time prevent stale bridge files from being accepted after PID reuse.

`displayName` prefers an explicit Pi `/name`, then a deterministic title derived from the first user prompt, then the project directory. `titleSource` identifies which rule produced the value.

## Snapshot API

The persistent service exposes the same `DashboardSnapshot` through `GET /v1/snapshot` and `GET /v1/events`. The one-shot `pi-beacon snapshot` command remains a compatibility surface:

```json
{
  "version": 1,
  "generatedAt": "2026-08-29T18:00:00+02:00",
  "runtime": {
    "sessions": [],
    "childSessions": [],
    "subagents": [],
    "agents": [],
    "activity": [],
    "unattachedAgents": [],
    "modelActivity": [],
    "sessionCount": 0,
    "subagentCount": 0,
    "activeCount": 0,
    "attention": false
  },
  "today": {
    "cost": 0,
    "tokens": 0,
    "messages": 0,
    "sessions": 0,
    "recent": []
  },
  "history": {
    "dailyCost": [],
    "modelUsageToday": [],
    "modelUsage7d": []
  },
  "update": {
    "currentVersion": "1.0.0",
    "latestVersion": "1.1.0",
    "available": true,
    "releaseUrl": "https://github.com/andres-ortizl/pi-beacon/releases/tag/v1.1.0",
    "checkedAt": "2026-08-30T18:00:00+00:00"
  }
}
```

The Pydantic models in `pi_beacon.models` are the canonical contract. JSON uses camelCase aliases. Python callers can import `DashboardSnapshot` and use snake_case fields.

`runtime.activity` is the normalized execution tree for maintained frontends. Its roots are parent Pi sessions and its children are associated subagents. Every normalized `AgentStatus` also includes additive `parentIdentity` and `parentSessionId` when an unambiguous live process-parent relationship can be resolved. `runtime.unattachedAgents` retains live agents whose parent cannot be established without guessing; the raw `childSessions` and `subagents` arrays remain diagnostic input surfaces. `runtime.modelActivity` groups only live processes that reported their own model.

`history.dailyCost` contains a seven-day, zero-filled daily cost trend. `history.modelUsageToday` and `history.modelUsage7d` aggregate assistant responses by the concrete `responseModel` (or `model`) on each response. Usage without a concrete assistant model remains in overall cost and token totals but is deliberately excluded from model rankings.

`update` reads the last successful manual or opt-in timer check from the local cache. The observability service never performs the network request itself. When no check has run, `latestVersion` and `releaseUrl` are empty and `available` is false.

## Incremental index

The SQLite cache stores one source cursor per JSONL file plus retained daily aggregates:

- last file size and modification time;
- source device and inode;
- last complete byte offset and a bounded cursor fingerprint;
- session identity and project metadata;
- all billable daily usage and activity for each source;
- assistant-response usage grouped by that response's concrete model.

If a file grows, Pi Beacon validates the bytes before the saved offset and reads only the append. A changed inode, shrink, or fingerprint mismatch deletes that source's retained aggregates and recomputes it from byte zero. Incomplete final lines are not committed. Missing sources remove their aggregate rows, and daily rows older than the configured retention period are pruned. Existing cache cursors are automatically rebuilt when the aggregate format or configured retention period changes.

SQLModel defines the tables. SQLAlchemy async and `aiosqlite` perform service I/O. Alembic owns schema migrations.

## Compatibility

Breaking payload changes require a new top-level `version`. Breaking HTTP route changes require a new router prefix. Additive fields do not require a version change. Consumers must ignore fields they do not recognize.

See [api-v1.md](api-v1.md) for transport, SSE, revision, lifecycle, and security guarantees. See [subagent-adapter-v1.md](subagent-adapter-v1.md) for normalized child-agent provenance.
