# Contributing

## Setup

```bash
uv sync --locked --dev
uv run pre-commit install --hook-type pre-commit --hook-type pre-push
```

## Required checks

```bash
uv run ruff check .
uv run ruff format --check .
uv run ty check
uv run pytest
uv build
```

Keep changes focused. Add a regression test for parser, index, bridge, and public API changes. Do not weaken the coverage threshold or add broad ignores to satisfy a checker.

## Public contract

`src/pi_beacon/models.py` defines the snapshot contract. Keep JSON aliases camelCase and Python attributes snake_case. Consumers must tolerate additive fields. Use a new top-level schema version for breaking changes.

The TypeScript extension must keep live bridge writes atomic and local. Do not add telemetry or network listeners without an explicit design change and documentation.
