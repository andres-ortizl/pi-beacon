# pi-subagents adapter v1

Pi does not define a core subagent protocol. Pi Beacon therefore treats `pi-subagents` lifecycle files as an optional, versioned adapter rather than part of the Pi SDK contract.

## Compatibility

Adapter identity:

```text
name: pi-subagents
adapterVersion: 1
supported lifecycleArtifactVersion: 3
```

The adapter ignores artifacts with a missing or unsupported `lifecycleArtifactVersion`, invalid UTF-8, or fields that fail typed validation. A malformed artifact does not hide valid sibling artifacts. This fails closed when the producer changes its schema. Supporting another producer version requires fixtures, parser changes, and a new compatibility entry in this document.

## Discovery

The default root is:

```text
$TMPDIR/pi-subagents-uid-<uid>/async-subagent-runs
```

Override it with:

```toml
[paths]
subagent_runs_dir = "/private/path/to/async-subagent-runs"
```

or `PI_BEACON_PATHS__SUBAGENT_RUNS_DIR`.

Pi Beacon recursively reads `status.json` below this root. It never modifies producer artifacts.

## Accepted input

A v1 adapter input requires:

- `lifecycleArtifactVersion: 3`;
- an active top-level `state`;
- optional active `steps`;
- a `runId` or `id` when available;
- agent, task, state, timing, PID, and parent PID fields when supplied by the producer.

Active states are `queued`, `running`, `paused`, `waiting`, and `needs_attention`. Terminal artifacts are excluded from the live agent collection.

## Normalized output

Every accepted item becomes an `AgentStatus` with:

- stable `identity`;
- `source: "pi-subagents"`;
- `sourceVersion: 1`;
- agent and task labels;
- normalized state and elapsed time;
- run ID, PID, parent PID, and start time when available.

Child Pi processes discovered independently use `source: "pi-process"`. Pi Beacon deduplicates an artifact-backed agent and child process only when PID and Linux process start time match, and any producer instance IDs do not conflict. It preserves both when durable correlation is absent.

The raw `runtime.childSessions` and `runtime.subagents` arrays remain additive diagnostic fields. Maintained frontends render the normalized `runtime.agents` collection.

## Privacy

Lifecycle artifacts can contain task descriptions and local paths. The adapter returns bounded operational fields but callers must still treat the API as private user data. Tests use synthetic fixtures and must not commit real lifecycle artifacts.
