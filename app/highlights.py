from __future__ import annotations

import re
from typing import Any

HOOK_TERMS = {
    "why", "how", "secret", "mistake", "truth", "learned", "never", "always", "biggest",
    "surprising", "surprise", "instead", "because", "first", "finally", "important", "remember",
    "problem", "solution", "answer", "actually", "imagine", "watch", "stop", "best", "worst",
}


def _utterances(payload: dict[str, Any]) -> list[dict[str, Any]]:
    results = payload.get("results") or {}
    raw = results.get("utterances") or []
    if raw:
        return [{"start": float(u.get("start", 0)), "end": float(u.get("end", 0)),
                 "text": str(u.get("transcript", "")).strip(), "speaker": u.get("speaker")}
                for u in raw if u.get("transcript") and float(u.get("end", 0)) > float(u.get("start", 0))]
    try:
        words = results["channels"][0]["alternatives"][0]["words"]
    except (KeyError, IndexError, TypeError):
        return []
    chunks: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for word in words:
        current.append(word)
        text = str(word.get("punctuated_word") or word.get("word") or "")
        span = float(current[-1].get("end", 0)) - float(current[0].get("start", 0))
        if text.endswith((".", "?", "!")) or span >= 6:
            chunks.append({"start": float(current[0].get("start", 0)), "end": float(current[-1].get("end", 0)),
                           "text": " ".join(str(w.get("punctuated_word") or w.get("word") or "") for w in current),
                           "speaker": None})
            current = []
    if current:
        chunks.append({"start": float(current[0].get("start", 0)), "end": float(current[-1].get("end", 0)),
                       "text": " ".join(str(w.get("punctuated_word") or w.get("word") or "") for w in current),
                       "speaker": None})
    return [u for u in chunks if u["end"] > u["start"] and u["text"]]


def _score(text: str, duration: float) -> int:
    words = re.findall(r"[\w']+", text.lower())
    if not words:
        return 0
    hooks = sum(1 for token in words if token in HOOK_TERMS)
    questions = text.count("?")
    exclamations = text.count("!")
    numbers = len(re.findall(r"\b\d+(?:\.\d+)?\b", text))
    density = min(len(words) / max(duration, 1), 3.5)
    duration_bonus = 12 if 25 <= duration <= 60 else 5 if 18 <= duration <= 75 else 0
    return min(100, 28 + hooks * 8 + questions * 7 + exclamations * 3 + numbers * 3 + int(density * 5) + duration_bonus)


def suggest_highlights(payload: dict[str, Any], media_duration: float, limit: int = 8) -> list[dict[str, Any]]:
    utterances = _utterances(payload)
    if not utterances:
        return []
    candidates: list[dict[str, Any]] = []
    next_sample_at = -1.0
    for i, first in enumerate(utterances):
        start = max(0.0, first["start"] - 0.25)
        if start < next_sample_at:
            continue
        target = start + 42.0
        possible = []
        for item in utterances[i:]:
            if item["end"] - start < 18:
                continue
            if item["end"] - start > 62:
                break
            possible.append(item)
        if not possible:
            continue
        ending = min(possible, key=lambda item: abs(item["end"] - target))
        end = min(float(media_duration), float(ending["end"]) + 0.15)
        if end - start < 15:
            continue
        text = " ".join(item["text"] for item in utterances[i:] if item["start"] < end and item["end"] > start).strip()
        if len(re.findall(r"[\w']+", text)) < 20:
            continue
        score = _score(text, end - start)
        title = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0][:96].strip() or "Suggested moment"
        candidates.append({"start_seconds": round(start, 2), "end_seconds": round(end, 2),
                           "transcript": text, "title": title, "score": score})
        next_sample_at = start + 16
    ranked = sorted(candidates, key=lambda c: (-c["score"], c["start_seconds"]))
    selected: list[dict[str, Any]] = []
    for candidate in ranked:
        overlaps = any(min(candidate["end_seconds"], chosen["end_seconds"]) - max(candidate["start_seconds"], chosen["start_seconds"]) > 18
                       for chosen in selected)
        if not overlaps:
            selected.append(candidate)
        if len(selected) >= max(1, limit):
            break
    return sorted(selected, key=lambda c: c["start_seconds"])
