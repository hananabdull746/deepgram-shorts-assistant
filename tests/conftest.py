from __future__ import annotations

import base64
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "ClipCue-Test-Password-2026")
os.environ.setdefault("SESSION_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("DATA_DIR", "/tmp/clipcue-test-data")
os.environ.setdefault("DATABASE_PATH", "/tmp/clipcue-test-data/clipcue.sqlite3")
os.environ.setdefault("DEEPGRAM_API_KEY", "")
os.environ.setdefault("PUBLIC_BASE_URL", "")

import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import Database
from app.worker import Worker


@pytest.fixture
def application(tmp_path, monkeypatch):
    db = Database(tmp_path / "clipcue.sqlite3")
    worker = Worker(db)
    monkeypatch.setattr(worker, "start", lambda: None)
    monkeypatch.setattr(worker, "stop", lambda: None)
    monkeypatch.setattr(main, "DB", db)
    monkeypatch.setattr(main, "WORKER", worker)
    with TestClient(main.app) as client:
        yield client, db, worker


def login(client: TestClient) -> str:
    import re
    page = client.get("/login")
    csrf = re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
    response = client.post("/login", data={"username": "admin", "password": "ClipCue-Test-Password-2026", "csrf": csrf}, follow_redirects=False)
    assert response.status_code == 303
    page = client.get("/")
    return re.search(r'name="csrf" value="([^"]+)"', page.text).group(1)
