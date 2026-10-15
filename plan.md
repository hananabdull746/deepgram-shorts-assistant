# ClipCue implementation plan

## Product scope

ClipCue is a separate, self-hosted assistant for preparing short vertical clips from videos the operator owns or is authorized to edit. It accepts a local video upload, sends a compressed audio track to Deepgram for transcription, recommends timecoded moments, and renders selected clips for manual review. It does not download source videos from platforms or publish to YouTube automatically.

## Implementation approach

- Python 3.10+ FastAPI app, server-rendered Jinja pages, SQLite persistence, and one in-process background worker.
- Deepgram prerecorded Speech-to-Text REST API (`nova-3`, punctuated/smart-formatted utterances) called with the operator's `DEEPGRAM_API_KEY`. Persist the returned transcript so a later restart does not repeat a successful billable transcription.
- FFmpeg extracts mono low-bitrate MP3 from the source and renders selected segments to 720×1280 H.264/AAC with burned captions plus downloadable SRT/VTT sidecars.
- A transparent local scoring heuristic proposes candidate moments from timestamped transcript utterances; the user edits timestamps/title and chooses what to render. No second LLM key is required.
- Optional Deepgram callback mode supports long transcriptions when `PUBLIC_BASE_URL` points to a publicly reachable HTTPS reverse proxy. Without it, a serial worker waits on the synchronous Deepgram request.
- Basic administrator login, CSRF checks, and local disk storage. API key and session secrets live in a root-readable environment file outside the repository.

## 1 GB VPS constraints

Run one transcription/render job at a time. Stream uploads to disk, keep media out of memory, use one FFmpeg thread and the ultrafast preset, output 720×1280, and avoid local ML models. Require a source no larger than 1080p for predictable memory use; users with 4K originals should create a 1080p proxy before upload. Keep configurable limits on upload size, duration, disk use, and clip length. Transcription costs are incurred at Deepgram; never retry a completed transcript automatically.

## Project structure

- `app/main.py`: authenticated web routes, upload, project review, callbacks, clip render requests, and file downloads.
- `app/db.py`: SQLite schema and persistence helpers.
- `app/deepgram.py`: Deepgram prerecorded HTTP client and response normalization.
- `app/media.py`: ffprobe/FFmpeg audio extraction, caption generation, and vertical clip rendering.
- `app/highlights.py`: deterministic candidate-window scoring from timestamped utterances.
- `app/worker.py`: one serial background worker for transcription and rendering jobs.
- `app/templates/` and `app/static/`: lightweight dashboard, project editor, and styling.
- `install.sh`, `deploy/clipcue.service`: Ubuntu 22.04/24.04 installation and systemd service.
- `tests/`: mocked Deepgram, scoring, media command, persistence, and web-auth coverage.

## Design direction

Three visual directions considered:

- **Editorial timeline desk — 0.07:** charcoal surfaces, restrained mint timing marks, dense but calm project metadata.
- **Warm analog edit bay — 0.04:** ivory canvas, graphite controls, and amber editorial notes.
- **Broadcast control room — 0.02:** deep navy, bright cyan status labels, and compact technical readouts.

Chosen: **Editorial timeline desk**.

- **Design Movement:** modern editorial software with restrained broadcast-console cues.
- **Core Principles:** show source-to-clip lineage; keep timestamps visible; make suggestions editable; separate machine work from human approval.
- **Color Philosophy:** charcoal reduces visual noise, warm white improves transcript reading, and mint marks successful states without making approval automatic.
- **Layout Paradigm:** a left rail for project navigation and a wide reading/editing pane; candidate moments appear as a chronological strip rather than a generic dashboard card grid.
- **Signature Elements:** timecode chips; transcript blocks with aligned start/end values; a narrow mint progress indicator.
- **Interaction Philosophy:** make every expensive or destructive step explicit; upload, transcribe, and render are separate actions with status feedback.
- **Animation:** brief opacity/width transitions for progress only; no looping motion.
- **Typography System:** system sans for UI, monospace numerals for timecodes, clear 14–16 px body text.
- **Brand Essence:** a practical editor's assistant that turns long recordings into reviewable vertical cuts; precise, calm, candid.
- **Brand Voice:** clear and editorial. Examples: “Transcript ready. Pick the moment; you control the cut.” “Deepgram is transcribing; this page can stay open or you can return later.”
- **Wordmark & Logo:** simple `CLIP / CUE` wordmark with a mint vertical edit marker; no decorative illustration required.
- **Signature Brand Color:** soft mint `#8BE3C0`.
