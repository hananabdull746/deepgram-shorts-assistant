from __future__ import annotations

from pathlib import Path
from typing import Any

import requests

API_URL = "https://api.deepgram.com/v1/listen"


class DeepgramError(RuntimeError):
    pass


def transcribe_file(
    audio_path: str | Path,
    api_key: str,
    model: str = "nova-3",
    language: str = "en",
    callback_url: str = "",
    timeout_seconds: int = 570,
) -> dict[str, Any]:
    if not api_key:
        raise DeepgramError("DEEPGRAM_API_KEY is not configured on this server")
    params: dict[str, str] = {
        "model": model,
        "language": language,
        "smart_format": "true",
        "punctuate": "true",
        "utterances": "true",
    }
    if callback_url:
        params["callback"] = callback_url
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": "audio/mpeg",
        "Accept": "application/json",
    }
    try:
        with Path(audio_path).open("rb") as audio:
            response = requests.post(API_URL, params=params, headers=headers,
                                     data=audio, timeout=(20, timeout_seconds))
    except requests.RequestException as exc:
        raise DeepgramError(f"Could not reach Deepgram: {exc.__class__.__name__}") from exc
    if not response.ok:
        detail = response.text[:600].replace(api_key, "[redacted]")
        raise DeepgramError(f"Deepgram returned HTTP {response.status_code}: {detail}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise DeepgramError("Deepgram returned a response that was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise DeepgramError("Deepgram returned an unexpected response")
    return payload
