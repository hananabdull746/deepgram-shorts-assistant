from __future__ import annotations

import re
import uuid

from conftest import login


def test_dashboard_requires_login_and_login_rotates_session(application):
    client, db, worker = application
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    csrf = re.search(r'name="csrf" value="([^"]+)"', client.get("/login").text).group(1)
    bad_csrf = client.post("/login", data={"username": "admin", "password": "ClipCue-Test-Password-2026", "csrf": "wrong"}, follow_redirects=False)
    assert bad_csrf.status_code == 403
    token = login(client)
    page = client.get("/")
    assert page.status_code == 200 and "Project desk" in page.text
    assert token in page.text


def test_upload_is_authenticated_and_stored_under_random_name(application):
    client, db, worker = application
    token = login(client)
    response = client.post("/projects/upload", data={"csrf": token, "title": "My Episode"},
                           files={"video": ("episode.mp4", b"test-video-bytes", "video/mp4")},
                           follow_redirects=False)
    assert response.status_code == 303
    project_id = response.headers["location"].split("/projects/")[1].split("?")[0]
    project = db.get_project(project_id)
    assert project["title"] == "My Episode"
    assert project["source_path"].endswith(".mp4")
    assert project["original_name"] == "episode.mp4"
    assert open(project["source_path"], "rb").read() == b"test-video-bytes"
    assert client.get(f"/projects/{project_id}/source").content == b"test-video-bytes"


def test_callback_requires_per_project_token_and_returns_200(application, monkeypatch):
    client, db, worker = application
    project_id = uuid.uuid4().hex
    db.create_project({"id": project_id, "title": "Callback", "original_name": "source.mp4", "source_path": "/tmp/no-file.mp4"})
    token = "random-callback-token"
    db.update_project(project_id, status="waiting_callback", duration=120, callback_token=token)
    calls = []
    monkeypatch.setattr(worker, "accept_callback", lambda job_id, payload: calls.append((job_id, payload)))
    denied = client.post(f"/deepgram/callback/{project_id}?token=wrong", json={"results": {}})
    assert denied.status_code == 404
    accepted = client.post(f"/deepgram/callback/{project_id}?token={token}", json={"results": {"channels": []}})
    assert accepted.status_code == 200
    assert calls == [(project_id, {"results": {"channels": []}})]


def test_health_endpoint_is_public_and_does_not_require_deepgram_key(application):
    client, _, _ = application
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True
