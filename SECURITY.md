# Security notes

ClipCue is designed for a single administrator on a self-hosted VPS. The installer binds Uvicorn to `127.0.0.1:8090`; use the SSH tunnel or a carefully configured HTTPS reverse proxy. Do not expose the app directly to the public internet.

The Deepgram API key, admin password, and session secret belong in `/etc/clipcue/clipcue.env`, not in source code, Git, browser forms, or logs. The installer creates the environment file with root ownership and group-only read access. Rotate a key if it is exposed.

Source videos, extracted audio, transcript responses, and finished clips live under `/var/lib/clipcue`. The app gates project and download routes behind the administrator session. Back up the data directory and environment file securely; both media and credentials are sensitive.

Optional Deepgram callbacks are unauthenticated by browser login by design. Each request URL includes a cryptographically random per-project token checked with constant-time comparison. Configure callback mode only behind a public HTTPS reverse proxy. Do not log full callback URLs or query tokens.

Only process media you own or are authorized to use. ClipCue does not attempt to bypass platform access controls or automatically publish output.
