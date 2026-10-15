from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db import Database
from app.deepgram import DeepgramError, transcribe_file
from app.highlights import suggest_highlights
from app.media import MediaError, caption_cues, probe_video, render_vertical_clip, write_captions
from app.worker import Worker


def sample_transcript():
    utterances = []
    for index in range(45):
        start = index * 4.0
        text = "Why this surprising idea works? The biggest mistake is ignoring the simple answer."
        words = []
        tokens = text.split()
        step = 3.5 / len(tokens)
        for pos, word in enumerate(tokens):
            words.append({"word": word.strip("?.!"), "punctuated_word": word,
                          "start": start + pos * step, "end": start + (pos + 1) * step})
        utterances.append({"start": start, "end": start + 3.6, "transcript": text,
                           "speaker": 0, "words": words})
    return {"metadata": {"duration": 180}, "results": {"utterances": utterances,
            "channels": [{"alternatives": [{"transcript": "Sample transcript", "words": []}]}]}}


def test_deepgram_upload_uses_key_timestamps_and_returns_payload(tmp_path, monkeypatch):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"audio bytes")
    captured = {}
    class Response:
        ok = True
        text = ""
        def json(self):
            return {"results": {"channels": []}, "metadata": {"request_id": "abc"}}
    def fake_post(url, **kwargs):
        captured.update(kwargs)
        captured["url"] = url
        captured["body"] = kwargs["data"].read()
        return Response()
    monkeypatch.setattr("app.deepgram.requests.post", fake_post)
    payload = transcribe_file(audio, "secret-key", callback_url="https://example.org/hook?token=abc")
    assert payload["metadata"]["request_id"] == "abc"
    assert captured["headers"]["Authorization"] == "Token secret-key"
    assert captured["params"]["utterances"] == "true"
    assert captured["params"]["callback"].startswith("https://")
    assert captured["body"] == b"audio bytes"


def test_deepgram_missing_key_and_http_error_are_clear_and_redacted(tmp_path, monkeypatch):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x")
    with pytest.raises(DeepgramError, match="DEEPGRAM_API_KEY"):
        transcribe_file(audio, "")
    class Response:
        ok = False
        status_code = 401
        text = "bad key secret-key"
    monkeypatch.setattr("app.deepgram.requests.post", lambda *a, **k: Response())
    with pytest.raises(DeepgramError) as error:
        transcribe_file(audio, "secret-key")
    assert "secret-key" not in str(error.value)


def test_highlight_suggestions_are_timecoded_and_bounded():
    suggestions = suggest_highlights(sample_transcript(), 180, limit=5)
    assert 1 <= len(suggestions) <= 5
    assert all(0 <= item["start_seconds"] < item["end_seconds"] <= 180 for item in suggestions)
    assert all(15 <= item["end_seconds"] - item["start_seconds"] <= 62 for item in suggestions)
    assert any(item["score"] > 50 for item in suggestions)


def test_highlights_handles_empty_payload():
    assert suggest_highlights({}, 60) == []


def test_database_claims_one_job_and_recovers_rendering(tmp_path):
    db = Database(tmp_path / "queue.sqlite3")
    db.create_project({"id": "project", "title": "Source", "original_name": "source.mp4", "source_path": "/tmp/source.mp4"})
    claimed = db.claim_project()
    assert claimed["id"] == "project" and claimed["status"] == "transcribing"
    db.update_project("project", status="ready", duration=60, transcript_json=json.dumps(sample_transcript()))
    db.create_clip({"id": "clip", "project_id": "project", "title": "Moment", "start_seconds": 1,
                    "end_seconds": 30, "transcript": "text", "score": 60})
    assert db.claim_clip()["status"] == "rendering"
    db.init()
    assert db.get_clip("clip")["status"] == "queued"


def test_worker_persists_transcript_and_does_not_duplicate_candidates(tmp_path):
    db = Database(tmp_path / "worker.sqlite3")
    db.create_project({"id": "project", "title": "Source", "original_name": "source.mp4", "source_path": "/tmp/source.mp4"})
    db.update_project("project", status="transcribing", duration=180, callback_token="callback-secret")
    worker = Worker(db)
    payload = sample_transcript()
    worker.complete_transcription("project", payload, 180)
    first_count = db.count_clips("project")
    worker.complete_transcription("project", payload, 180)
    project = db.get_project("project")
    assert project["status"] == "ready"
    assert project["callback_token"] == "callback-secret"
    assert project["transcript_json"]
    assert 1 <= first_count <= 8
    assert db.count_clips("project") == first_count


def test_probe_video_accepts_full_hd_and_rejects_4k(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"fake")
    payload = {"format": {"duration": "60"}, "streams": [
        {"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}]}
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout=json.dumps(payload))
    monkeypatch.setattr("app.media.subprocess.run", fake_run)
    assert probe_video(video)["duration"] == 60
    payload["streams"][0]["width"] = 3840
    payload["streams"][0]["height"] = 2160
    with pytest.raises(MediaError, match="1080p"):
        probe_video(video)


def test_caption_cues_are_clipped_and_caption_files_written(tmp_path):
    payload = sample_transcript()
    cues = caption_cues(payload, 0, 20)
    assert cues and all(0 <= cue[0] < cue[1] <= 20 for cue in cues)
    srt, vtt = tmp_path / "clip.srt", tmp_path / "clip.vtt"
    write_captions(payload, 0, 20, srt, vtt)
    assert "-->" in srt.read_text()
    assert vtt.read_text().startswith("WEBVTT")


def test_render_command_is_single_thread_and_vertical(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    output = tmp_path / "out.mp4"
    srt = tmp_path / "captions.srt"
    source.touch()
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    captured = {}
    def fake_run(command, **kwargs):
        captured["command"] = command
        Path(command[-1]).write_bytes(b"rendered")
    monkeypatch.setattr("app.media.subprocess.run", fake_run)
    render_vertical_clip(source, output, srt, 5, 35)
    command = captured["command"]
    assert "720:1280" in command[command.index("-vf") + 1]
    assert command.count("1") >= 2
    assert output.read_bytes() == b"rendered"
