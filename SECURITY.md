# Security

Pi Beacon processes local Pi session logs, which can contain prompts, paths, tool calls, and model metadata. Treat the SQLite cache and runtime bridge as private user data.

- Live bridge files are local and user-readable only.
- Pi Beacon does not upload session data or telemetry.
- Manual update checks, and the disabled-by-default update timer when enabled, request only public release metadata from GitHub.
- Pi Beacon does not open a TCP listener by default.
- The HTTP API uses a private Unix socket in the user runtime directory.
- The Unix socket and SQLite files are user-readable only.
- Quickshell and Waybar receive bounded summaries, not full message content.

Report security issues privately to the repository owner rather than opening a public issue with session data attached.
