# ClipCue — Deepgram Shorts assistant

ClipCue is a separate self-hosted workspace for preparing vertical clips from videos you own or are authorized to edit. Upload a local video, have Deepgram transcribe its audio, review timecoded candidate moments, adjust the cut, and render a 720×1280 MP4 with burned captions plus SRT/VTT files. ClipCue does not fetch YouTube videos and does not auto-publish.

## Requirements and 1 GB VPS profile

- Ubuntu Server 22.04 or 24.04, 1 GB RAM, and enough disk for the uploaded source plus generated clips. A small swap file is recommended if the VPS provider permits it.
- A Deepgram API key with prerecorded Speech-to-Text access. Transcription is billed by Deepgram; check your Deepgram account's current plan and usage before processing.
- Source videos must have audio, be no larger than 1080p (approximately 2.1 megapixels), be no longer than 3 hours, and fit the configured upload limit (default 2 GiB). Make a 1080p proxy for 4K input before uploading.

The app uses one background worker, one FFmpeg thread, low-bitrate mono MP3 for transcription, and no local AI model. The original and resulting clips stay on the VPS. Only extracted audio is sent to Deepgram. Results are persisted so a restart does not automatically submit a successful transcription again. A render can be retried because it is local processing.

## Install on Ubuntu

Authenticate the VPS to GitHub first; do not paste a personal access token into a clone URL or shell history. With GitHub CLI already installed and authenticated, clone with `gh repo clone hananabdull746/deepgram-shorts-assistant`. Or configure an SSH deploy key and use the SSH clone URL. Then run the installer from the repository root:

```bash
git clone git@github.com:hananabdull746/deepgram-shorts-assistant.git
cd deepgram-shorts-assistant
sudo bash ./install.sh
```

The installer supports Ubuntu 22.04/24.04, installs FFmpeg and Python dependencies, creates a dedicated `clipcue` service account, protects `/etc/clipcue/clipcue.env`, and installs a single-worker systemd service. It creates a random initial admin password and prints it only during first install.

Add your Deepgram key and restart the service:

```bash
sudo nano /etc/clipcue/clipcue.env
# set DEEPGRAM_API_KEY=your_real_key
sudo systemctl restart clipcue
```

The app binds only to loopback port 8090. On your workstation, open an SSH tunnel:

```bash
ssh -L 8090:127.0.0.1:8090 ubuntu@YOUR_SERVER_IP
```

Then open `http://localhost:8090`. The installer does not open a public firewall port.

## Long-video callback mode

Without callback configuration, the serial worker waits for Deepgram's synchronous prerecorded response. Deepgram documents a 10-minute processing-time limit for synchronous Nova/Base/Enhanced requests. For longer jobs, configure a public HTTPS reverse proxy to the loopback app, set `PUBLIC_BASE_URL=https://your-domain.example` in `/etc/clipcue/clipcue.env`, and set `COOKIE_SECURE=true`; then restart the service. ClipCue sends a tokenized per-project callback URL to Deepgram. Keep that proxy reachable while jobs are processing. If you only use an SSH tunnel, leave `PUBLIC_BASE_URL` empty and use synchronous mode.

## Workflow

1. Sign in with the generated admin credentials.
2. Upload a supported video file that you own or are authorized to edit.
3. ClipCue probes the media, extracts a 16 kHz mono MP3, and sends the audio to Deepgram using Nova-3 with timestamped utterances and smart formatting.
4. ClipCue proposes up to eight candidate windows using a transparent transcript heuristic. It is not a semantic guarantee; inspect and edit the title and start/end times.
5. Render selected candidates. The output is a center-cropped 720×1280 H.264/AAC MP4 with captions. Download the MP4, SRT, or VTT.
6. Review content, captions, rights, and platform rules before publishing manually.

If candidates are not useful, create a custom moment by entering the times. Clips are limited to 10–180 seconds. The score is a simple editorial cue based on wording, punctuation, duration, and speech density—not a viral prediction.

## API key and privacy

Deepgram is called directly by the app using `Authorization: Token ...`; the key is never sent to the browser or written into Git. Uploaded videos and transcript JSON are stored under `/var/lib/clipcue`. Protect backups of that directory and `/etc/clipcue/clipcue.env`. Only audio is sent to Deepgram for transcription. Do not upload content you lack permission to process.

## Update, status, and logs

```bash
cd ~/deepgram-shorts-assistant
git pull --ff-only origin main
sudo bash ./install.sh
sudo systemctl status clipcue --no-pager
sudo journalctl -u clipcue -n 100 --no-pager
```

The installer restarts the service after copying updated files. To check the local health endpoint: `curl http://127.0.0.1:8090/health`.

## Tests

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Tests mock Deepgram; a live API key is not included. A real transcription request was not made during development.

## Official Deepgram references

- [Prerecorded audio guide](https://developers.deepgram.com/docs/pre-recorded-audio)
- [Pre-recorded Listen API reference](https://developers.deepgram.com/reference/speech-to-text/listen-pre-recorded)
- [Callback guide](https://developers.deepgram.com/docs/using-callbacks-to-return-transcripts-to-your-server)
- [Timestamps and utterances](https://deepgram.com/learn/working-with-timestamps-utterances-and-speaker-diarization-in-deepgram)
