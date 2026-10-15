from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class MediaError(RuntimeError):
    pass


def probe_video(path: str | Path, max_duration: int = 10800, max_pixels: int = 2073600) -> dict[str, Any]:
    command = ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
               "-of", "json", str(path)]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=45)
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise MediaError("Could not inspect the video. Check that it is a supported video file.") from exc
    streams = data.get("streams", [])
    video = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if not video:
        raise MediaError("The uploaded file does not contain a video stream")
    if not any(stream.get("codec_type") == "audio" for stream in streams):
        raise MediaError("The uploaded video has no audio track to transcribe")
    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    try:
        duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    except (ValueError, TypeError):
        duration = 0
    if width <= 0 or height <= 0 or width * height > max_pixels or max(width, height) > 1920:
        raise MediaError("For this 1 GB server, upload a video proxy no larger than 1080p (about 2.1 megapixels).")
    if duration <= 0:
        raise MediaError("Could not determine the video duration")
    if duration > max_duration:
        raise MediaError(f"Video exceeds the configured limit of {max_duration // 3600} hours")
    return {"width": width, "height": height, "duration": duration}


def extract_audio(source: str | Path, destination: str | Path) -> None:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "1",
               "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k",
               "-codec:a", "libmp3lame", "-f", "mp3", str(destination)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Audio extraction exceeded its 15-minute safety limit") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaError("FFmpeg could not extract audio from this source") from exc
    if not destination.exists() or destination.stat().st_size == 0:
        raise MediaError("FFmpeg produced an empty audio file")


def _timestamp(seconds: float, decimal: str = ",") -> str:
    ms = max(0, int(round(seconds * 1000)))
    hours, ms = divmod(ms, 3_600_000)
    minutes, ms = divmod(ms, 60_000)
    secs, ms = divmod(ms, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal}{ms:03d}"


def caption_cues(payload: dict[str, Any], clip_start: float, clip_end: float) -> list[tuple[float, float, str]]:
    try:
        utterances = payload["results"]["utterances"]
    except (KeyError, TypeError):
        utterances = []
    cues: list[tuple[float, float, str]] = []
    for utterance in utterances:
        words = utterance.get("words") or []
        if not words:
            start = max(clip_start, float(utterance.get("start", 0)))
            end = min(clip_end, float(utterance.get("end", 0)))
            text = str(utterance.get("transcript", "")).strip()
            if end > start and text:
                cues.append((start - clip_start, end - clip_start, text.replace("\n", " ")))
            continue
        group: list[tuple[float, float, str]] = []
        def flush() -> None:
            if group:
                cues.append((group[0][0] - clip_start, group[-1][1] - clip_start,
                             " ".join(word[2] for word in group).replace("\n", " ")))
                group.clear()
        for word in words:
            start = max(clip_start, float(word.get("start", 0)))
            end = min(clip_end, float(word.get("end", 0)))
            text = str(word.get("punctuated_word") or word.get("word") or "").strip()
            if end <= start or not text:
                continue
            if group and (len(group) >= 7 or start - group[0][0] >= 3.0):
                flush()
            group.append((start, end, text))
            if text.endswith((".", "?", "!")):
                flush()
        flush()
    return cues


def write_captions(payload: dict[str, Any], clip_start: float, clip_end: float,
                   srt_path: str | Path, vtt_path: str | Path) -> None:
    cues = caption_cues(payload, clip_start, clip_end)
    srt_lines: list[str] = []
    vtt_lines: list[str] = ["WEBVTT", ""]
    for index, (start, end, text) in enumerate(cues, 1):
        srt_lines.extend([str(index), f"{_timestamp(start)} --> {_timestamp(end)}", text, ""])
        vtt_lines.extend([f"{_timestamp(start, '.') } --> {_timestamp(end, '.')}", text, ""])
    Path(srt_path).write_text("\n".join(srt_lines), encoding="utf-8")
    Path(vtt_path).write_text("\n".join(vtt_lines), encoding="utf-8")


def render_vertical_clip(source: str | Path, output: str | Path, srt_path: str | Path,
                         start_seconds: float, end_seconds: float) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path = str(Path(srt_path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    vf = ("scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,"
          f"subtitles='{subtitle_path}':force_style='FontName=DejaVu Sans,FontSize=25,"
          "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=3,"
          "Shadow=1,Alignment=2,MarginV=120'")
    duration = end_seconds - start_seconds
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start_seconds:.3f}",
               "-threads", "1", "-i", str(source), "-t", f"{duration:.3f}", "-vf", vf,
               "-filter_threads", "1", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "27",
               "-pix_fmt", "yuv420p", "-threads", "1", "-c:a", "aac", "-b:a", "96k",
               "-movflags", "+faststart", str(output)]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=max(300, int(duration * 20)))
    except subprocess.TimeoutExpired as exc:
        raise MediaError("Clip render exceeded its safety limit") from exc
    except (OSError, subprocess.CalledProcessError) as exc:
        raise MediaError("FFmpeg could not render the clip. Check FFmpeg and subtitle support.") from exc
    if not output.exists() or output.stat().st_size == 0:
        raise MediaError("FFmpeg produced an empty clip")
