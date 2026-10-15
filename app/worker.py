from __future__ import annotations

import json
import logging
import secrets
import threading
from pathlib import Path
from typing import Any

from . import config
from .db import Database
from .deepgram import DeepgramError, transcribe_file
from .highlights import suggest_highlights
from .media import MediaError, extract_audio, probe_video, render_vertical_clip, write_captions

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, db: Database):
        self.db = db
        self.settings = config.settings
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run, name="clipcue-worker", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=4)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                if self._process_project_once():
                    continue
                if self._process_clip_once():
                    continue
            except Exception:
                # Individual job handlers persist user-safe errors; this protects the loop.
                logger.exception("Unexpected error in ClipCue worker loop")
            self.stop_event.wait(1.5)

    def _process_project_once(self) -> bool:
        project = self.db.claim_project()
        if not project:
            return False
        project_dir = self.settings.projects_dir / project["id"]
        project_dir.mkdir(parents=True, exist_ok=True)
        audio_path = project_dir / "audio.mp3"
        try:
            if not self.settings.deepgram_api_key:
                raise DeepgramError("Set DEEPGRAM_API_KEY in the server environment, then retry this project")
            info = probe_video(project["source_path"], self.settings.max_video_seconds, self.settings.max_video_pixels)
            extract_audio(project["source_path"], audio_path)
            self.db.update_project(project["id"], audio_path=str(audio_path), duration=info["duration"])
            callback_url = ""
            if self.settings.public_base_url:
                if not self.settings.public_base_url.startswith("https://"):
                    raise DeepgramError("PUBLIC_BASE_URL must be a public HTTPS URL for callback mode")
                token = secrets.token_urlsafe(32)
                self.db.update_project(project["id"], callback_token=token)
                callback_url = f"{self.settings.public_base_url}/deepgram/callback/{project['id']}?token={token}"
            payload = transcribe_file(audio_path, self.settings.deepgram_api_key,
                                      model=self.settings.deepgram_model,
                                      language=self.settings.deepgram_language,
                                      callback_url=callback_url,
                                      timeout_seconds=self.settings.deepgram_timeout_seconds)
            if callback_url and payload.get("request_id") and not payload.get("results"):
                self.db.set_waiting_callback_if_processing(project["id"], str(payload["request_id"]))
                return True
            self.complete_transcription(project["id"], payload, info["duration"])
        except (MediaError, DeepgramError, OSError, ValueError) as exc:
            self.db.update_project(project["id"], status="failed", error=str(exc)[:700])
        except Exception as exc:
            self.db.update_project(project["id"], status="failed", error=f"Unexpected processing error: {exc.__class__.__name__}")
        finally:
            # Keep audio only while Deepgram is processing or awaiting its callback.
            current = self.db.get_project(project["id"])
            if current and current["status"] not in {"waiting_callback", "transcribing"}:
                audio_path.unlink(missing_ok=True)
        return True

    def complete_transcription(self, project_id: str, payload: dict[str, Any], duration: float | None = None) -> None:
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        if project.get("transcript_json"):
            return
        if not isinstance(payload.get("results"), dict) or not payload["results"].get("channels"):
            self.db.update_project(project_id, status="failed", error="Deepgram callback did not contain a transcript")
            return
        duration = float(duration or project.get("duration") or payload.get("metadata", {}).get("duration") or 0)
        candidates = suggest_highlights(payload, duration, limit=min(8, self.settings.max_clips_per_project))
        clip_rows = [{"id": secrets.token_urlsafe(16), "title": item["title"],
                      "start_seconds": item["start_seconds"], "end_seconds": item["end_seconds"],
                      "transcript": item["transcript"], "score": item["score"]} for item in candidates]
        self.db.save_transcript(project_id, payload, duration, clips=clip_rows)

    def accept_callback(self, project_id: str, payload: dict[str, Any]) -> None:
        project = self.db.get_project(project_id)
        if not project:
            raise ValueError("Unknown project")
        if project.get("status") == "ready":
            return
        if payload.get("err_code") or payload.get("err_msg"):
            self.db.update_project(project_id, status="failed", error="Deepgram callback reported a transcription error")
            return
        self.complete_transcription(project_id, payload, project.get("duration"))
        audio_path = project.get("audio_path")
        if audio_path:
            Path(audio_path).unlink(missing_ok=True)

    def _process_clip_once(self) -> bool:
        clip = self.db.claim_clip()
        if not clip:
            return False
        project = self.db.get_project(clip["project_id"])
        if not project or not project.get("transcript_json"):
            self.db.update_clip(clip["id"], status="failed", error="Source transcript is unavailable")
            return True
        clip_dir = self.settings.projects_dir / clip["project_id"] / "clips"
        clip_dir.mkdir(parents=True, exist_ok=True)
        output = clip_dir / f"{clip['id']}.mp4"
        srt = clip_dir / f"{clip['id']}.srt"
        vtt = clip_dir / f"{clip['id']}.vtt"
        try:
            payload: dict[str, Any] = json.loads(project["transcript_json"])
            write_captions(payload, float(clip["start_seconds"]), float(clip["end_seconds"]), srt, vtt)
            render_vertical_clip(project["source_path"], output, srt,
                                 float(clip["start_seconds"]), float(clip["end_seconds"]))
            self.db.update_clip(clip["id"], status="complete", output_path=str(output),
                                srt_path=str(srt), vtt_path=str(vtt), error=None)
        except (MediaError, OSError, ValueError) as exc:
            self.db.update_clip(clip["id"], status="failed", error=str(exc)[:700])
        except Exception as exc:
            self.db.update_clip(clip["id"], status="failed", error=f"Unexpected render error: {exc.__class__.__name__}")
        return True
