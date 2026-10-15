from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def init(self) -> None:
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                original_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                audio_path TEXT,
                status TEXT NOT NULL,
                duration REAL,
                transcript_json TEXT,
                summary TEXT,
                error TEXT,
                callback_token TEXT,
                deepgram_request_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS clips (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL,
                transcript TEXT NOT NULL DEFAULT '',
                score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                output_path TEXT,
                srt_path TEXT,
                vtt_path TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_projects_status_created ON projects(status, created_at);
            CREATE INDEX IF NOT EXISTS idx_clips_status_created ON clips(status, created_at);
            """)
            # A process crash mid-render is safe to retry locally. A transcription may have
            # been accepted and billed remotely, so leave that one for explicit user retry.
            db.execute("UPDATE clips SET status='queued', error='Render interrupted; queued again after restart', updated_at=? WHERE status='rendering'", (int(time.time()),))
            db.execute("UPDATE projects SET status='failed', error='Interrupted during transcription. Retry manually; Deepgram may have received the request.', updated_at=? WHERE status='transcribing'", (int(time.time()),))

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self.connect() as db:
            db.execute(sql, params)

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def create_project(self, values: dict[str, Any]) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute("""INSERT INTO projects
                (id,title,original_name,source_path,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)""",
                (values["id"], values["title"], values["original_name"], values["source_path"], "queued", now, now))

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM projects WHERE id=?", (project_id,))

    def list_projects(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,))

    def claim_project(self) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM projects WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE projects SET status='transcribing', error=NULL, updated_at=? WHERE id=?", (int(time.time()), row["id"]))
            result = dict(row)
            result["status"] = "transcribing"
            return result

    def update_project(self, project_id: str, **fields: Any) -> None:
        allowed = {"status", "audio_path", "duration", "transcript_json", "summary", "error", "callback_token", "deepgram_request_id"}
        if not fields or set(fields) - allowed:
            raise ValueError("Unsupported project update")
        fields["updated_at"] = int(time.time())
        assignments = ",".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [project_id]
        self.execute(f"UPDATE projects SET {assignments} WHERE id=?", tuple(values))

    def set_waiting_callback_if_processing(self, project_id: str, request_id: str) -> None:
        self.execute("UPDATE projects SET status='waiting_callback', deepgram_request_id=?, updated_at=? WHERE id=? AND status='transcribing'",
                     (request_id, int(time.time()), project_id))

    def save_transcript(self, project_id: str, payload: dict[str, Any], duration: float,
                        summary: str = "", clips: list[dict[str, Any]] | None = None) -> bool:
        now = int(time.time())
        with self.connect() as db:
            updated = db.execute("""UPDATE projects SET status='ready', transcript_json=?, duration=?, summary=?,
                                  error=NULL, updated_at=? WHERE id=? AND transcript_json IS NULL""",
                                 (json.dumps(payload), duration, summary, now, project_id))
            if updated.rowcount == 0:
                return False
            for clip in clips or []:
                db.execute("""INSERT INTO clips
                    (id,project_id,title,start_seconds,end_seconds,transcript,score,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (clip["id"], project_id, clip["title"], clip["start_seconds"], clip["end_seconds"],
                     clip.get("transcript", ""), int(clip.get("score", 0)), "queued", now, now))
        return True

    def create_clip(self, values: dict[str, Any]) -> None:
        now = int(time.time())
        with self.connect() as db:
            db.execute("""INSERT INTO clips
                (id,project_id,title,start_seconds,end_seconds,transcript,score,status,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (values["id"], values["project_id"], values["title"], values["start_seconds"], values["end_seconds"],
                 values.get("transcript", ""), int(values.get("score", 0)), "queued", now, now))

    def list_clips(self, project_id: str) -> list[dict[str, Any]]:
        return self.all("SELECT * FROM clips WHERE project_id=? ORDER BY start_seconds,created_at", (project_id,))

    def get_clip(self, clip_id: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM clips WHERE id=?", (clip_id,))

    def claim_clip(self) -> dict[str, Any] | None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM clips WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE clips SET status='rendering', error=NULL, updated_at=? WHERE id=?", (int(time.time()), row["id"]))
            result = dict(row)
            result["status"] = "rendering"
            return result

    def update_clip(self, clip_id: str, **fields: Any) -> None:
        allowed = {"title", "start_seconds", "end_seconds", "transcript", "score", "status",
                   "output_path", "srt_path", "vtt_path", "error"}
        if not fields or set(fields) - allowed:
            raise ValueError("Unsupported clip update")
        fields["updated_at"] = int(time.time())
        assignments = ",".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [clip_id]
        self.execute(f"UPDATE clips SET {assignments} WHERE id=?", tuple(values))

    def count_clips(self, project_id: str) -> int:
        row = self.one("SELECT COUNT(*) AS n FROM clips WHERE project_id=?", (project_id,))
        return int(row["n"] if row else 0)
