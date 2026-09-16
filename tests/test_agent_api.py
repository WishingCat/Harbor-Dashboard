import hashlib
import io
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
from urllib.parse import parse_qs, urlparse
import zipfile

import pytest
from fastapi.testclient import TestClient

_module_data = tempfile.TemporaryDirectory(prefix="harbor-agent-api-tests-")
os.environ["HARBOR_DATA_DIR"] = _module_data.name
os.environ["HARBOR_SEED_DEMO"] = "false"
from server.main import create_app  # noqa: E402
from server import agent_api  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("HARBOR_PUBLIC_URL", raising=False)
    return create_app(tmp_path / "data", seed=False)


@pytest.fixture
def client(app):
    with TestClient(app) as connection:
        yield connection


def register(client, email="owner@example.com"):
    response = client.post("/api/auth/register", json={"name": email.split("@")[0], "email": email, "password": "test-password-long"})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def mint(client, project="project-aa", **fields):
    response = client.post("/api/auth/tokens", json={"name": "Agent uploader", "project_id": project, **fields})
    assert response.status_code == 200, response.text
    return response.json()


def headers(token, key=None):
    result = {"Authorization": f"Bearer {token}"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


def make_zip(entries, reverse=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        entries = list(entries.items())
        for path, content in reversed(entries) if reverse else entries:
            archive.writestr(path, content)
    return stream.getvalue()


def post_task(client, token, key=None, *, content=b"# Archived task", description="", project=None, reverse=False):
    data = {"description": description}
    if project is not None:
        data["project_id"] = project
    archive = make_zip({"task/task.toml": b'version="1.0"', "task/instruction.md": content}, reverse=reverse)
    return client.post("/api/v1/tasks", headers=headers(token, key), data=data, files=[("files", ("agent-task.zip", archive, "application/zip"))])


def post_rollout(client, task_id, token, key=None, *, name="existing-trial", project=None):
    data = {"name": name}
    if project is not None:
        data["project_id"] = project
    result = json.dumps({"trial_name": "archived-run", "agent_info": {"name": "codex"}, "verifier_result": {"rewards": {"reward": 1}}})
    return client.post(f"/api/v1/tasks/{task_id}/rollouts", headers=headers(token, key), data=data, files=[("files", ("result.json", result, "application/json"))])


def test_tokens_store_only_hash_and_return_secret_once_with_personal_ownership(client, app):
    owner = register(client)
    issued = mint(client)
    secret, metadata = issued["token"], issued["api_token"]
    assert secret.startswith("hbr_") and len(secret) >= 40
    assert metadata["last_used_at"] is None and metadata["revoked"] is False and metadata["revoked_at"] is None
    lifetime = datetime.fromisoformat(metadata["expires_at"]) - datetime.fromisoformat(metadata["created_at"])
    assert lifetime == timedelta(days=30)
    with app.state.store.connect() as db:
        row = dict(db.execute("SELECT * FROM api_tokens WHERE id=?", (metadata["id"],)).fetchone())
        assert row["user_id"] == owner["id"]
        assert row["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
        assert secret not in row.values()
    response = client.get("/api/auth/tokens")
    assert response.json()["tokens"] == [metadata]
    assert response.headers["cache-control"] == "no-store"
    assert secret not in response.text and "token_hash" not in response.text
    assert client.get("/api/v1/projects", headers=headers(secret)).status_code == 200
    assert client.get("/api/auth/tokens").json()["tokens"][0]["last_used_at"] is not None
    with TestClient(app) as other:
        register(other, "other@example.com")
        assert other.get("/api/auth/tokens").json()["tokens"] == []
        assert other.delete(f"/api/auth/tokens/{metadata['id']}").status_code == 404
    assert client.delete(f"/api/auth/tokens/{metadata['id']}").json() == {"ok": True}
    assert client.delete(f"/api/auth/tokens/{metadata['id']}").status_code == 200
    revoked = client.get("/api/auth/tokens").json()["tokens"][0]
    assert revoked["revoked"] and revoked["revoked_at"]
    assert client.get("/api/v1/projects", headers=headers(secret)).status_code == 401


@pytest.mark.parametrize("fields", [{"expires_in_days": 0}, {"expires_in_days": 366}, {"name": " "}, {"name": "x" * 81}])
def test_token_validation_rejects_invalid_name_or_expiry(client, fields):
    register(client)
    response = client.post("/api/auth/tokens", json={"name": "Test", "project_id": "project-aa", **fields})
    assert response.status_code == 422
    assert client.get("/api/auth/tokens").json()["tokens"] == []


def test_bearer_is_separate_from_cookie_sessions_and_expired_tokens_fail(client, app):
    register(client)
    issued = mint(client, expires_in_days=365)
    secret = issued["token"]
    assert client.get("/api/v1/projects").status_code == 401
    assert client.get("/api/v1/projects", headers=headers("hbr_invalid")).status_code == 401
    assert client.get("/api/v1/projects", headers={"Authorization": "Basic invalid"}).status_code == 401
    with TestClient(app) as bearer_only:
        bearer_only.headers.update(headers(secret))
        assert bearer_only.get("/api/auth/me").json()["user"] is None
        assert bearer_only.get("/api/auth/tokens").status_code == 401
        assert bearer_only.post("/api/auth/tokens", json={"name": "Cannot mint", "project_id": "project-aa"}).status_code == 401
        assert bearer_only.post("/api/tasks", files=[("files", ("task.zip", b"invalid"))]).status_code == 401
        assert bearer_only.put("/api/settings/translation", json={"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "model", "api_key": "secret"}).status_code == 401
        assert bearer_only.post("/api/translate", json={"file_id": "unknown", "target_language": "zh"}).status_code == 401
    with app.state.store.connect() as db:
        db.execute("UPDATE api_tokens SET expires_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), issued["api_token"]["id"]))
    response = client.get("/api/v1/projects", headers=headers(secret))
    assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"


def test_token_project_scope_and_author_permissions_are_enforced(client, app):
    register(client)
    aa = mint(client)["token"]
    paper = mint(client, "paperbenchx")["token"]
    aa_task = post_task(client, aa).json()["task"]
    paper_task = post_task(client, paper).json()["task"]
    for token, project, own, foreign in ((aa, "project-aa", aa_task, paper_task), (paper, "paperbenchx", paper_task, aa_task)):
        assert [p["id"] for p in client.get("/api/v1/projects", headers=headers(token)).json()["projects"]] == [project]
        assert [t["id"] for t in client.get("/api/v1/tasks", headers=headers(token)).json()["tasks"]] == [own["id"]]
        assert client.get(f"/api/v1/tasks/{foreign['id']}", headers=headers(token)).status_code == 404
        assert client.get("/api/v1/tasks?project_id=unknown", headers=headers(token)).status_code == 404
        assert post_task(client, token, project=foreign["project_id"]).status_code == 404
        assert post_rollout(client, foreign["id"], token).status_code == 404
    with TestClient(app) as member:
        register(member, "member@example.com")
        member_token = mint(member)["token"]
        assert post_rollout(member, aa_task["id"], member_token).status_code == 403
        member_task = post_task(member, member_token).json()["task"]
        assert post_rollout(member, member_task["id"], member_token).status_code == 200
        assert post_rollout(client, member_task["id"], aa).status_code == 200


def test_idempotency_replays_logical_content_without_duplicate_activity_and_rejects_changes(client, app):
    register(client)
    token = mint(client)["token"]
    key = "private-retry-key-unique"
    first = post_task(client, token, key, description="Initial summary")
    second = post_task(client, token, key, description="Initial summary", reverse=True, project="project-aa")
    assert first.status_code == second.status_code == 200
    assert first.json()["replayed"] is False and second.json()["replayed"] is True
    assert first.json()["task"]["id"] == second.json()["task"]["id"]
    assert key not in first.text and key not in second.text
    assert post_task(client, token, key, description="Changed summary").status_code == 409
    assert post_task(client, token, key, description="Initial summary", content=b"# Changed document").status_code == 409
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM api_idempotency").fetchone()[0] == 1
    task_id = first.json()["task"]["id"]
    rollout = post_rollout(client, task_id, token, key)
    replay = post_rollout(client, task_id, token, key)
    assert rollout.status_code == replay.status_code == 200
    assert replay.json()["replayed"] is True and rollout.json()["rollout"]["id"] == replay.json()["rollout"]["id"]
    assert post_rollout(client, task_id, token, key, name="changed-name").status_code == 409
    detail = client.get(f"/api/v1/tasks/{task_id}", headers=headers(token)).json()
    assert detail["task"]["rollouts_count"] == 1 and detail["task"]["reviews_count"] == 0


def test_idempotency_scope_is_per_token_and_actual_route(client):
    register(client)
    token_one, token_two = mint(client)["token"], mint(client)["token"]
    first = post_task(client, token_one, "shared-key").json()["task"]
    second = post_task(client, token_two, "shared-key").json()["task"]
    assert first["id"] != second["id"]
    assert post_rollout(client, first["id"], token_one, "shared-key").status_code == 200
    assert post_rollout(client, second["id"], token_one, "shared-key").status_code == 200


@pytest.mark.parametrize("different_payloads", [False, True])
def test_concurrent_idempotency_creates_only_one_task(client, app, different_payloads):
    register(client)
    token = mint(client)["token"]
    barrier = Barrier(2)
    def upload(index):
        with TestClient(app) as connection:
            barrier.wait(timeout=10)
            return post_task(connection, token, "concurrent-key", content=f"# Document {index if different_payloads else 0}".encode())
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(upload, (0, 1)))
    assert sorted(response.status_code for response in responses) == ([200, 409] if different_payloads else [200, 200])
    if not different_payloads:
        assert sorted(response.json()["replayed"] for response in responses) == [False, True]
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM api_idempotency WHERE response_json IS NOT NULL").fetchone()[0] == 1
    assert len(list(app.state.store.blobs.iterdir())) == 2


def test_failed_idempotent_import_rolls_back_reservation_files_and_can_retry(client, app, monkeypatch):
    register(client)
    token = mint(client)["token"]
    original = app.state.store.add_file
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated full disk")
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.store, "add_file", fail_second)
    with TestClient(app, raise_server_exceptions=False) as connection:
        assert post_task(connection, token, "retry-after-failure").status_code == 500
    with app.state.store.connect() as db:
        for table in ("tasks", "files", "activity", "api_idempotency"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert list(app.state.store.blobs.iterdir()) == []
    monkeypatch.setattr(app.state.store, "add_file", original)
    retry = post_task(client, token, "retry-after-failure")
    assert retry.status_code == 200 and retry.json()["replayed"] is False
    assert post_task(client, token, "retry-after-failure").json()["replayed"] is True


@pytest.mark.parametrize("key", ["", "   ", "x" * 129])
def test_invalid_idempotency_key_does_not_import(client, key):
    register(client)
    token = mint(client)["token"]
    assert post_task(client, token, key).status_code == 422
    assert client.get("/api/v1/tasks", headers=headers(token)).json()["tasks"] == []


def test_agent_zip_safety_reuses_archive_validation(client):
    register(client)
    token = mint(client)["token"]
    unsafe = make_zip({"../escape.txt": b"unsafe"})
    response = client.post("/api/v1/tasks", headers=headers(token, "safe-retry"), files=[("files", ("unsafe.zip", unsafe))])
    assert response.status_code == 400
    assert post_task(client, token, "safe-retry").status_code == 200


def test_corrupt_zip_returns_400_and_idempotency_key_can_retry_without_partial_writes(client, app):
    register(client)
    token = mint(client)["token"]
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("instruction.md", b"Stored instructions")
    corrupted = bytearray(stream.getvalue())
    # A reserved deflate block type damages the payload while retaining a valid
    # ZIP central directory; Python raises zlib.error instead of BadZipFile.
    corrupted[30 + len("instruction.md")] = 7
    response = client.post("/api/v1/tasks", headers=headers(token, "corrupt-retry"), files=[("files", ("damaged.zip", bytes(corrupted), "application/zip"))])
    assert response.status_code == 400 and "ZIP" in response.json()["detail"]
    with app.state.store.connect() as db:
        for table in ("tasks", "files", "activity", "api_idempotency"):
            assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert list(app.state.store.blobs.iterdir()) == []
    retry = post_task(client, token, "corrupt-retry")
    assert retry.status_code == 200 and retry.json()["replayed"] is False
    assert post_task(client, token, "corrupt-retry").json()["replayed"] is True


def test_links_public_base_review_state_and_bearer_openapi(client, monkeypatch):
    register(client)
    token = mint(client, "paperbenchx")["token"]
    monkeypatch.setenv("HARBOR_PUBLIC_URL", "https://harbor.example.test")
    response = post_task(client, token)
    task_id = response.json()["task"]["id"]
    task_set_id = response.json()["task"]["task_set_id"]
    assert urlparse(response.json()["api_url"]).path == f"/api/v1/tasks/{task_id}"
    assert urlparse(response.json()["api_url"]).netloc == "harbor.example.test"
    assert parse_qs(urlparse(response.json()["api_url"]).query) == {"project_id": ["paperbenchx"], "task_set_id": [task_set_id]}
    assert parse_qs(urlparse(response.json()["task_url"]).query) == {"task": [task_id], "project": ["paperbenchx"], "task_set": [task_set_id]}
    rollout = post_rollout(client, task_id, token).json()
    assert parse_qs(urlparse(rollout["task_url"]).query)["rollout"] == [rollout["rollout"]["id"]]
    client.post(f"/api/tasks/{task_id}/reviews?project_id=paperbenchx", json={"verdict": "changes_requested", "body": "Add an edge case"})
    detail = client.get(f"/api/v1/tasks/{task_id}", headers=headers(token)).json()
    assert detail["task"]["status"] == "changes_requested" and detail["reviews"][0]["body"] == "Add an edge case"
    monkeypatch.delenv("HARBOR_PUBLIC_URL")
    details = client.get(f"/api/v1/tasks/{task_id}", headers={**headers(token), "X-Forwarded-Host": "evil.example", "Forwarded": "host=evil.example"}).json()
    assert details["task_url"].startswith("http://testserver/")
    schema = client.get("/openapi.json").json()
    assert schema["components"]["securitySchemes"]["AgentBearer"]["scheme"] == "bearer"
    assert schema["paths"]["/api/v1/tasks"]["post"]["security"] == [{"AgentBearer": []}]
    assert schema["paths"]["/api/v1/tasks"]["post"]["tags"] == ["Agent API v1"]
    for path in ("/api/v1/tasks", "/api/v1/tasks/{task_id}/rollouts"):
        examples = schema["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["examples"]
        assert examples["created"]["value"]["replayed"] is False
        assert examples["replayed"]["value"]["replayed"] is True


def test_private_api_responses_never_cache_success_auth_or_early_errors(client):
    register(client)
    issued = mint(client)
    responses = [
        client.get("/api/auth/tokens"),
        client.get("/api/v1/projects", headers=headers(issued["token"])),
        client.get("/api/v1/projects", headers=headers("hbr_invalid")),
        client.post("/api/auth/tokens", headers={"Origin": "https://other.example"}, json={}),
        client.post("/api/v1/tasks", headers={"Content-Length": str(60 * 1024 * 1024)}),
    ]
    assert [response.status_code for response in responses] == [200, 200, 401, 403, 413]
    assert all(response.headers["cache-control"] == "no-store" for response in responses)


def test_agent_client_download_is_fixed_public_attachment_and_missing_file_returns_404(client, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_api, "__file__", str(tmp_path / "server" / "agent_api.py"))
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "harbor_upload.py"
    script.write_text("# Credential-free client fixture\n", encoding="utf-8")
    response = client.get("/api/agent-client.py")
    assert response.status_code == 200 and response.content == script.read_bytes()
    assert "attachment" in response.headers["content-disposition"] and "harbor_upload.py" in response.headers["content-disposition"]
    script.unlink()
    assert client.get("/api/agent-client.py").status_code == 404
