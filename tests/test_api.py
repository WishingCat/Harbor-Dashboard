import io
import hashlib
import json
import os
import sqlite3
import stat
import tempfile
import zipfile

import httpx
import pytest
from fastapi.testclient import TestClient

# Importing the ASGI entry point must not create the developer's real database.
_module_data = tempfile.TemporaryDirectory(prefix="harbor-module-tests-")
os.environ["HARBOR_DATA_DIR"] = _module_data.name
os.environ["HARBOR_SEED_DEMO"] = "false"
from server.main import RequestBodyLimit, create_app, password_hash  # noqa: E402
from server.imports import parse_rollout  # noqa: E402
from server.storage import uid  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    for name in ("TRANSLATION_API_KEY", "TRANSLATION_MANAGED", "TRANSLATION_PROVIDER", "TRANSLATION_BASE_URL", "TRANSLATION_MODEL", "TRANSLATION_THINKING", "TRANSLATION_TRUST_ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return create_app(tmp_path / "db", seed=False)


@pytest.fixture
def client(app):
    with TestClient(app) as client:
        yield client


def register(client, email="owner@example.com", name="Owner"):
    response = client.post("/api/auth/register", json={"name": name, "email": email, "password": "correct-horse-password"})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def insert_imported_test_user(app, username="ceshi", password="ceshi", email="test.member@example.com", external_id="lark:tenant-test:open-id-test"):
    """Synthetic import fixture in the isolated test database, never a real member."""
    user_id = uid()
    with app.state.store.connect() as db:
        db.execute("INSERT INTO users (id,name,email,password_hash,role,created_at,username,external_id) VALUES (?,?,?,?,?,?,?,?)", (
            user_id, "测试成员", email, password_hash(password), "member", "2026-01-01T00:00:00+00:00", username, external_id,
        ))
    return user_id


def upload_task(client, entries=None, title="A real uploaded task", project_id=None):
    entries = entries or {"task/instruction.md": b"# Repair the parser\n\nPreserve Unicode input.", "task/task.toml": b'version = "1.0"\n'}
    data = {"title": title, "description": "Uploaded in integration test", "category": "software-engineering", "difficulty": "hard", "tags": '["Python"]', "paths": json.dumps(list(entries))}
    if project_id is not None:
        data["project_id"] = project_id
    return client.post("/api/tasks", data=data,
                       files=[("files", (path.rsplit("/", 1)[-1], content, "application/octet-stream")) for path, content in entries.items()])


def archive(entries, symlink=False, compressed=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED if compressed else zipfile.ZIP_STORED) as handle:
        for path, content in entries.items():
            if symlink:
                info = zipfile.ZipInfo(path)
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                handle.writestr(info, content)
            else:
                handle.writestr(path, content)
    return stream.getvalue()


def zip_task(client, payload):
    return client.post("/api/tasks", data={"title": "Archive task"}, files=[("files", ("task.zip", payload, "application/zip"))])


def test_auth_sessions_roles_and_persistence(client, app):
    assert client.get("/api/auth/me").json() == {"user": None}
    assert upload_task(client).status_code == 401
    user = register(client)
    assert user["role"] == "admin"
    assert user["username"] is None and "external_id" not in user
    session = client.cookies.get("harbor_session")
    assert len(session) >= 40
    assert client.get("/api/auth/me").json()["user"] == user
    with app.state.store.connect() as db:
        stored = db.execute("SELECT password_hash FROM users").fetchone()[0]
        token = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert stored.startswith("scrypt$") and "correct-horse-password" not in stored
    assert token != session
    with TestClient(create_app(app.state.store.root, seed=False)) as reopened:
        reopened.cookies.set("harbor_session", session)
        assert reopened.get("/api/auth/me").json()["user"]["id"] == user["id"]
    client.post("/api/auth/logout")
    client.cookies.set("harbor_session", session)
    assert client.get("/api/auth/me").json()["user"] is None
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": user["email"], "password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"email": "OWNER@example.com", "password": "correct-horse-password"})
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
    client.post("/api/auth/logout")
    assert register(client, "reader@example.com", "Reader")["role"] == "member"
    assert client.post("/api/auth/register", json={"name": "Copy", "email": "OWNER@example.com", "password": "correct-horse-password"}).status_code == 409


def test_upload_public_read_download_and_activity(client):
    register(client)
    response = upload_task(client)
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["files_count"] == 2 and task["rollouts_count"] == 0 and not task["is_demo"]
    client.post("/api/auth/logout")
    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert [f["path"] for f in detail["files"]] == ["instruction.md", "task.toml"]
    file_id = detail["files"][0]["id"]
    assert "Preserve Unicode" in client.get(f"/api/files/{file_id}/content").json()["content"]
    download = client.get(f"/api/files/{file_id}/download")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert download.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/activity").json()["activity"][0]["task_id"] == task["id"]


def test_imported_username_login_accepts_short_pinyin_password_and_normalizes_identifier(client, app):
    register(client)
    client.post("/api/auth/logout")
    imported_id = insert_imported_test_user(app)
    with app.state.store.connect() as db:
        stored_hash = db.execute("SELECT password_hash FROM users WHERE id=?", (imported_id,)).fetchone()[0]
    assert stored_hash.startswith("scrypt$") and stored_hash != "ceshi"
    for credentials in (
        {"username": "ceshi"}, {"username": "  CeShI \t"},
        {"username": " TEST.MEMBER@EXAMPLE.COM "}, {"email": " TEST.MEMBER@EXAMPLE.COM "},
        {"email": "ceshi"}, {"username": "  ", "email": "test.member@example.com"},
    ):
        response = client.post("/api/auth/login", json={**credentials, "password": "ceshi"})
        assert response.status_code == 200, response.text
        user = response.json()["user"]
        assert user == {"id": imported_id, "name": "测试成员", "email": "test.member@example.com", "role": "member", "username": "ceshi"}
        assert "external_id" not in response.text and "lark:tenant-test" not in response.text
        assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=lax" in response.headers["set-cookie"]
        assert client.get("/api/auth/me").json()["user"] == user
        client.post("/api/auth/logout")
    for username, password in (("ceshi", "wrong"), ("ceshi", "CESHI"), ("ceshi", " ceshi "), ("ce%", "ceshi"), ("ces", "ceshi"), ("测试成员", "ceshi")):
        response = client.post("/api/auth/login", json={"username": username, "password": password})
        assert response.status_code == 401
        assert client.get("/api/auth/me").json()["user"] is None


@pytest.mark.parametrize("identifiers", [{}, {"username": None, "email": None}, {"username": "  "}, {"email": "\t "}, {"username": " ", "email": " "}])
def test_login_requires_nonempty_username_or_email(client, identifiers):
    response = client.post("/api/auth/login", json={**identifiers, "password": "ceshi"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], str)


def test_username_and_external_id_uniqueness_are_enforced_with_nullable_legacy_accounts(client, app):
    first = register(client)
    second = register(client, "second@example.com", "Second owner")
    assert first["username"] is None and second["username"] is None
    imported_id = insert_imported_test_user(app)
    with pytest.raises(sqlite3.IntegrityError):
        insert_imported_test_user(app, username="CESHI", email="collision@example.com", external_id="lark:tenant-test:another-open-id")
    with pytest.raises(sqlite3.IntegrityError):
        insert_imported_test_user(app, username="another", email="another@example.com")
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 3
        assert db.execute("SELECT id FROM users WHERE username='CESHI' COLLATE NOCASE").fetchone()[0] == imported_id
        assert db.execute("SELECT COUNT(*) FROM users WHERE username IS NULL AND external_id IS NULL").fetchone()[0] == 2
    # Self-registration keeps its existing minimum password length.
    assert client.post("/api/auth/register", json={"name": "Short password", "email": "short@example.com", "password": "ceshi"}).status_code == 422


def test_ambiguous_username_and_email_does_not_select_an_account_by_row_order(client, app):
    register(client, email="shared@example.com")
    client.post("/api/auth/logout")
    insert_imported_test_user(app, username="shared@example.com")
    for password in ("correct-horse-password", "ceshi"):
        response = client.post("/api/auth/login", json={"username": "shared@example.com", "password": password})
        assert response.status_code == 401
    assert client.get("/api/auth/me").json()["user"] is None


@pytest.mark.parametrize("path", ["../escape.txt", "/absolute.txt", "C:\\Windows\\escape.txt", "folder/../../escape.txt"])
def test_zip_slip_rejected_without_database_writes(client, path):
    register(client)
    response = zip_task(client, archive({path: b"unsafe"}))
    assert response.status_code == 400
    assert client.get("/api/tasks").json()["tasks"] == []


def test_zip_symlinks_bombs_and_duplicates_rejected(client):
    register(client)
    assert zip_task(client, archive({"link": b"/etc/passwd"}, symlink=True)).status_code == 400
    assert zip_task(client, archive({"oversized.txt": b"a" * (2 * 1024 * 1024)}, compressed=True)).status_code == 413
    assert zip_task(client, b"this is not a zip").status_code == 400
    response = client.post("/api/tasks", data={"title": "Duplicate", "paths": '["same.md","same.md"]'}, files=[("files", ("a.md", b"a")), ("files", ("b.md", b"b"))])
    assert response.status_code == 400


def trial_result(reward=1, name="trial-one"):
    return json.dumps({"task_name": "parser", "trial_name": name, "agent_info": {"name": "codex", "model_info": {"name": "test-model", "provider": "test"}}, "verifier_result": {"rewards": {"reward": reward}}, "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:02:03Z", "exception_info": None}).encode()


def test_trial_and_multi_trial_job_import_preserves_rewards(client, app):
    owner = register(client)
    task_id = upload_task(client).json()["task"]["id"]
    entries = {"jobs/job/result.json": b'{"stats":{},"n_total_trials":2}', "jobs/job/config.json": b'{"job_name":"original"}', "jobs/job/one/result.json": trial_result(),
               "jobs/job/one/agent/trajectory.json": b'{"steps":[]}', "jobs/job/one/_job/keep.txt": b"existing artifact", "jobs/job/two/result.json": trial_result(0.75, "trial-two"),
               "jobs/job/two/verifier/reward.txt": b"0.75"}
    response = client.post(f"/api/tasks/{task_id}/rollouts", files=[("files", ("job.zip", archive(entries), "application/zip"))])
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["imported_count"] == 2
    by_name = {r["name"]: r for r in data["rollouts"]}
    assert by_name["trial-one"]["status"] == "passed"
    assert by_name["trial-one"]["duration_seconds"] == 123
    assert by_name["trial-one"]["agent"] == "codex" and by_name["trial-one"]["model"] == "test-model"
    assert by_name["trial-two"]["reward"] == 0.75 and by_name["trial-two"]["status"] == "unknown"
    first_files = {f["path"]: f for f in by_name["trial-one"]["files"]}
    assert "_job/keep.txt" in first_files
    assert "_job_2/job/result.json" in first_files and "_job_2/job/config.json" in first_files
    assert client.get(f"/api/files/{first_files['_job_2/job/config.json']['id']}/download").content == entries["jobs/job/config.json"]
    assert sum(len(r["files"]) for r in data["rollouts"]) == len(entries)
    assert client.get(f"/api/tasks/{task_id}").json()["task"]["rollouts_count"] == 2
    with TestClient(app) as reader:
        register(reader, "other@example.com")
        response = reader.post(f"/api/tasks/{task_id}/rollouts", files=[("files", ("result.json", trial_result()))])
        assert response.status_code == 403


def test_task_bundle_embedded_rollout_is_separated(client):
    register(client)
    response = upload_task(client, {"bundle/instruction.md": b"# Task", "bundle/task.toml": b'version="1.0"',
                                    "bundle/rollouts/run/result.json": trial_result(), "bundle/rollouts/run/artifacts/report.md": b"# Report"})
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["files_count"] == 2 and task["rollouts_count"] == 1


def test_zip_name_automatically_names_task_and_empty_description_stays_empty(client):
    register(client)
    manifest = b'''schema_version = "1.4"
[task]
name = "internal/different-name"
description = "Do not fill the optional dashboard description automatically."
keywords = ["Python", "Numerics", "Python"]
[metadata]
category = "scientific-computing"
difficulty = "hard"
'''
    entries = {"internal-root/task.toml": manifest, "internal-root/instruction.md": b"# Uploaded task", "internal-root/tests/test.sh": b"exit 1\n"}
    response = client.post("/api/tasks", files=[("files", ("实验任务.v2.ZIP", archive(entries), "application/zip"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["title"] == "实验任务.v2" and task["description"] == ""
    assert task["category"] == "scientific-computing" and task["difficulty"] == "hard"
    assert task["tags"] == ["Python", "Numerics"]
    assert task["files_count"] == 3 and task["rollouts_count"] == 0 and task["reviews_count"] == 0
    assert task["status"] == "pending"
    # Old clients may still explicitly provide a title and metadata.
    legacy = client.post("/api/tasks", data={"title": "Explicit legacy title", "description": "Optional summary", "category": "data-science", "difficulty": "easy", "tags": "[]"}, files=[("files", ("ignored-automatic-name.zip", archive(entries), "application/zip"))])
    assert legacy.status_code == 200, legacy.text
    older = legacy.json()["task"]
    assert older["title"] == "Explicit legacy title" and older["description"] == "Optional summary"
    assert older["category"] == "data-science" and older["difficulty"] == "easy" and older["tags"] == []


@pytest.mark.parametrize("root,manifest,expected", [
    ("selected-task-folder/", b'[task]\nname="metadata/fallback"\n', "selected-task-folder"),
    ("", b'[task]\nname="metadata/fallback"\n', "metadata/fallback"),
    ("", b'version="1.0"\n', "未命名任务"),
])
def test_folder_root_then_toml_names_tasks_without_manual_fields(client, root, manifest, expected):
    register(client)
    paths = [root + "task.toml", root + "instruction.md"]
    response = client.post("/api/tasks", data={"paths": json.dumps(paths), "description": "   "}, files=[("files", ("task.toml", manifest)), ("files", ("instruction.md", b"# Task"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["title"] == expected and task["description"] == ""
    assert (task["category"], task["difficulty"], task["tags"]) == ("software-engineering", "medium", [])
    assert task["rollouts_count"] == 0


def test_invalid_optional_toml_metadata_uses_defaults_without_blocking_import(client):
    register(client)
    manifest = b'''[task]
name = 123
keywords = [1, true, " useful ", "useful"]
[metadata]
category = 42
difficulty = "expert"
'''
    response = client.post("/api/tasks", files=[("files", ("metadata-test.zip", archive({"task.toml": manifest, "instruction.md": b"# Task"}), "application/zip"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["title"] == "metadata-test"
    assert task["category"] == "software-engineering" and task["difficulty"] == "medium" and task["tags"] == ["useful"]


def test_task_result_fixtures_stay_task_material_and_upload_does_not_execute_them(client, tmp_path):
    register(client)
    never_created = tmp_path / "must-not-execute"
    entries = {"task.toml": b'version="1.0"', "instruction.md": b"# Task",
               "result.json": b'{"exception_info":null,"status":"done"}',
               "tests/result.json": trial_result(name="verifier-fixture"),
               "environment/data/result.json": trial_result(name="environment-fixture"),
               "solution/result.json": trial_result(name="solution-fixture"),
               "steps/prepare/result.json": trial_result(name="step-fixture"),
               "tests/test.sh": f'#!/bin/sh\ntouch "{never_created}"\n'.encode()}
    response = client.post("/api/tasks", files=[("files", ("fixtures.zip", archive(entries), "application/zip"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert task["files_count"] == len(entries) and detail["rollouts"] == []
    assert {file["path"] for file in detail["files"]} == set(entries)
    for file in detail["files"]:
        assert client.get(f"/api/files/{file['id']}/download").content == entries[file["path"]]
    assert not never_created.exists()


def test_task_zip_embedded_job_preserves_every_file_and_project_scope(client):
    register(client)
    entries = {
        "bundle/task/task.toml": b'version="1.0"',
        "bundle/task/instruction.md": b"# Task with archived results",
        "bundle/task/tests/result.json": trial_result(name="fixture-only"),
        "bundle/jobs/export/config.json": b'{"job_name":"export"}',
        "bundle/jobs/export/result.json": b'{"stats":{},"n_total_trials":2}',
        "bundle/jobs/export/one/result.json": trial_result(1, "run-one"),
        "bundle/jobs/export/one/agent/trajectory.json": b'{"steps":[{"message":"Existing artifact one"}]}',
        "bundle/jobs/export/one/artifacts/report.md": b"# Existing report one",
        "bundle/jobs/export/two/result.json": trial_result(0, "run-two"),
        "bundle/jobs/export/two/verifier/reward.txt": b"0\n",
        "bundle/jobs/export/two/artifacts/report.md": b"# Existing report two",
    }
    response = client.post("/api/tasks", data={"project_id": "paperbenchx"}, files=[("files", ("archived-task.zip", archive(entries), "application/zip"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["title"] == "archived-task" and task["project_id"] == "paperbenchx" and task["rollouts_count"] == 2
    detail = client.get(f"/api/tasks/{task['id']}?project_id=paperbenchx").json()
    assert {r["name"] for r in detail["rollouts"]} == {"run-one", "run-two"}
    files = [(file, "bundle/" + file["path"]) for file in detail["files"]]
    for rollout in detail["rollouts"]:
        prefix = "bundle/jobs/export/one/" if rollout["name"] == "run-one" else "bundle/jobs/export/two/"
        files.extend((file, prefix + file["path"]) for file in rollout["files"])
    assert len(files) == len(entries) and {original for _, original in files} == set(entries)
    for file, original in files:
        assert client.get(f"/api/files/{file['id']}/download?project_id=paperbenchx").content == entries[original]
        assert client.get(f"/api/files/{file['id']}/content?project_id=project-aa").status_code == 404
    assert client.get("/api/tasks?project_id=project-aa").json()["tasks"] == []


def test_root_trial_does_not_consume_task_material_under_parent_folder(client):
    register(client)
    entries = {"task/task.toml": b'version="1.0"', "task/instruction.md": b"# Task",
               "task/tests/result.json": trial_result(name="fixture-only"),
               "result.json": trial_result(name="existing-root-trial"), "agent/trajectory.json": b'{"steps":[]}'}
    response = client.post("/api/tasks", files=[("files", ("root-result.zip", archive(entries), "application/zip"))])
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["files_count"] == 3 and task["rollouts_count"] == 1
    detail = client.get(f"/api/tasks/{task['id']}").json()
    assert {file["path"] for file in detail["files"]} == {"task/task.toml", "task/instruction.md", "task/tests/result.json"}
    assert {file["path"] for file in detail["rollouts"][0]["files"]} == {"result.json", "agent/trajectory.json"}


@pytest.mark.parametrize("entries", [
    {"instruction.md": b"missing manifest"},
    {"task.toml": b'version="1.0"'},
    {"one/task.toml": b'version="1.0"', "one/instruction.md": b"one", "two/task.toml": b'version="1.0"', "two/instruction.md": b"two"},
    {"task.toml": b"not valid toml [", "instruction.md": b"task"},
])
def test_missing_invalid_and_multi_task_bundles_are_rejected(client, entries):
    register(client)
    response = upload_task(client, entries)
    assert response.status_code == 400 and isinstance(response.json()["detail"], str)
    assert client.get("/api/tasks").json()["tasks"] == []


def test_multistep_task_without_root_instruction_is_accepted(client):
    register(client)
    response = upload_task(client, {"task/task.toml": b'schema_version="1.4"', "task/steps/prepare/instruction.md": b"Prepare the input", "task/steps/solve/instruction.md": b"Solve the problem"})
    assert response.status_code == 200, response.text
    assert response.json()["task"]["files_count"] == 3


def test_named_metrics_do_not_claim_binary_success():
    parsed = parse_rollout({}, result={"verifier_result": {"rewards": {"loss": 0}}})
    assert parsed["reward"] == 0 and parsed["status"] == "unknown"
    parsed = parse_rollout({}, result={"verifier_result": {"rewards": {"precision": 0.8, "recall": 0.9}}})
    assert parsed["reward"] is None and parsed["status"] == "unknown"


@pytest.mark.parametrize("raw_reward,expected_reward", [(True, None), (False, None), (10**40, 1e40), (10**400, None)])
def test_rollout_numeric_boundaries_preserve_original_file_without_false_verdicts(client, raw_reward, expected_reward):
    register(client)
    task_id = upload_task(client).json()["task"]["id"]
    body = json.dumps({"trial_name": "boundary-trial", "agent_info": {"name": "test"}, "verifier_result": {"rewards": {"reward": raw_reward}}}).encode()
    response = client.post(f"/api/tasks/{task_id}/rollouts", files=[("files", ("result.json", body, "application/json"))])
    assert response.status_code == 200, response.text
    rollout = response.json()["rollout"]
    assert rollout["status"] == "unknown" and rollout["reward"] == expected_reward
    assert client.get(f"/api/files/{rollout['files'][0]['id']}/download").content == body
    assert client.get(f"/api/tasks/{task_id}").json()["task"]["status"] == "pending"


def test_failed_file_transaction_leaves_no_partial_import(client, app, monkeypatch):
    register(client)
    original = app.state.store.add_file
    calls = 0
    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("Simulated full disk")
        return original(*args, **kwargs)
    monkeypatch.setattr(app.state.store, "add_file", fail_second)
    with pytest.raises(OSError, match="Simulated full disk"):
        upload_task(client)
    assert client.get("/api/tasks").json()["tasks"] == []
    assert list(app.state.store.blobs.iterdir()) == []


def test_latest_decision_per_reviewer_and_comments_do_not_clear_verdict(client, app):
    register(client)
    task_id = upload_task(client).json()["task"]["id"]
    def review(who, verdict):
        response = who.post(f"/api/tasks/{task_id}/reviews", json={"verdict": verdict, "body": "Specific review feedback"})
        assert response.status_code == 200, response.text
        return client.get(f"/api/tasks/{task_id}").json()["task"]["status"]
    assert review(client, "approved") == "approved"
    with TestClient(app) as reviewer:
        register(reviewer, "reviewer@example.com")
        assert review(reviewer, "changes_requested") == "changes_requested"
        assert review(client, "approved") == "changes_requested"
        assert review(reviewer, "comment") == "changes_requested"
        assert review(reviewer, "approved") == "approved"
    assert client.get(f"/api/tasks/{task_id}").json()["task"]["reviews_count"] == 5


def test_translation_configuration_permissions_streaming_and_no_key_leak(client, app):
    register(client)
    task_id = upload_task(client).json()["task"]["id"]
    file_id = client.get(f"/api/tasks/{task_id}").json()["files"][0]["id"]
    assert client.post("/api/translate", json={"file_id": file_id, "target_language": "zh"}).status_code == 503
    config = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash", "api_key": "test-secret-key"}
    response = client.put("/api/settings/translation", json=config)
    assert response.status_code == 200 and response.json()["configured"]
    assert "test-secret-key" not in response.text and "api_key" not in response.json()
    config["api_key"] = ""
    assert client.put("/api/settings/translation", json=config).json()["configured"]
    switched = dict(config, base_url="https://another-provider.example/v1")
    assert client.put("/api/settings/translation", json=switched).status_code == 422
    assert client.get("/api/settings/translation").json()["base_url"] == config["base_url"]
    with TestClient(app) as member:
        register(member, "member@example.com")
        assert member.put("/api/settings/translation", json=config).status_code == 403
    def upstream(request):
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret-key"
        payload = json.loads(request.content)
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["messages"][1]["content"].startswith("# Repair")
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"修复"}}]}\n\ndata: {"choices":[{"delta":{"content":"解析器"}}]}\n\ndata: [DONE]\n\n', headers={"Content-Type": "text/event-stream"})
    app.state.translation_transport = httpx.MockTransport(upstream)
    response = client.post("/api/translate", json={"file_id": file_id, "target_language": "zh"})
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
    assert '"text": "修复"' in response.text and '"done": true' in response.text
    assert "test-secret-key" not in response.text
    app.state.translation_transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": "test-secret-key"}))
    response = client.post("/api/translate", json={"file_id": file_id, "target_language": "en"})
    assert '"error":' in response.text and "test-secret-key" not in response.text
    client.post("/api/auth/logout")
    assert client.post("/api/translate", json={"file_id": file_id, "target_language": "zh"}).status_code == 401


@pytest.mark.parametrize("base_url", ["https://[invalid", "https://provider.example:notaport", "https://provider.example:65536"])
def test_invalid_translation_url_returns_422_and_preserves_existing_configuration(client, base_url):
    register(client)
    original = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "test-model", "api_key": "test-secret"}
    assert client.put("/api/settings/translation", json=original).status_code == 200
    rejected = client.put("/api/settings/translation", json={**original, "base_url": base_url, "api_key": "another-test-key"})
    assert rejected.status_code == 422
    settings = client.get("/api/settings/translation")
    assert settings.json()["base_url"] == original["base_url"] and settings.json()["configured"]
    assert "test-secret" not in rejected.text + settings.text and "another-test-key" not in rejected.text + settings.text


def test_provider_switch_requires_explicit_key_even_with_environment_fallback(client, monkeypatch):
    register(client)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "environment-secret")
    assert client.get("/api/settings/translation").json()["configured"]
    config = {"provider": "Custom", "base_url": "https://custom.example/v1", "model": "translation-model", "api_key": ""}
    assert client.put("/api/settings/translation", json=config).status_code == 422
    config["api_key"] = "explicit-new-secret"
    response = client.put("/api/settings/translation", json=config)
    assert response.status_code == 200 and response.json()["base_url"] == config["base_url"]
    assert "explicit-new-secret" not in response.text and "environment-secret" not in response.text


def test_managed_translation_uses_server_gateway_and_is_available_to_members(client, app, monkeypatch):
    register(client)
    task_id = upload_task(client).json()["task"]["id"]
    file_id = client.get(f"/api/tasks/{task_id}").json()["files"][0]["id"]
    stale = {"provider": "Custom", "base_url": "https://stale.example/v1", "model": "old-model", "api_key": "old-database-secret"}
    assert client.put("/api/settings/translation", json=stale).status_code == 200
    monkeypatch.setenv("TRANSLATION_MANAGED", "true")
    monkeypatch.setenv("TRANSLATION_PROVIDER", "DeepSeek")
    monkeypatch.setenv("TRANSLATION_BASE_URL", "http://platform-gateway.example/v1")
    monkeypatch.setenv("TRANSLATION_MODEL", "deepseek-flash")
    monkeypatch.setenv("TRANSLATION_API_KEY", "platform-test-secret")
    monkeypatch.setenv("TRANSLATION_THINKING", "disabled")
    monkeypatch.setenv("TRANSLATION_TRUST_ENV", "false")
    settings = client.get("/api/settings/translation")
    assert settings.json() == {"provider": "DeepSeek", "base_url": "", "model": "deepseek-flash", "configured": True, "managed": True}
    assert "platform-test-secret" not in settings.text and "api_key" not in settings.json()
    assert "platform-gateway.example" not in settings.text
    # Even an administrator cannot replace platform routing through the browser.
    assert client.put("/api/settings/translation", json=stale).status_code == 403

    def upstream(request):
        assert str(request.url) == "http://platform-gateway.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer platform-test-secret"
        payload = json.loads(request.content)
        assert payload["model"] == "deepseek-flash"
        assert payload["thinking"] == {"type": "disabled"}
        assert payload["stream"] is True
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"修复解析器"},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')

    app.state.translation_transport = httpx.MockTransport(upstream)
    with TestClient(app) as member:
        assert member.post("/api/translate", json={"file_id": file_id, "target_language": "zh"}).status_code == 401
        assert register(member, "translation-member@example.com")["role"] == "member"
        response = member.post("/api/translate", json={"file_id": file_id, "target_language": "zh"})
        assert response.status_code == 200 and '"text": "修复解析器"' in response.text
        assert '"done": true' in response.text and '"error":' not in response.text
        assert "platform-test-secret" not in response.text and "old-database-secret" not in response.text


def test_managed_translation_does_not_fall_back_to_database_credentials(client, monkeypatch):
    register(client)
    config = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "old-model", "api_key": "database-secret"}
    assert client.put("/api/settings/translation", json=config).status_code == 200
    monkeypatch.setenv("TRANSLATION_MANAGED", "true")
    assert client.get("/api/settings/translation").json()["configured"] is False
    task_id = upload_task(client).json()["task"]["id"]
    file_id = client.get(f"/api/tasks/{task_id}").json()["files"][0]["id"]
    response = client.post("/api/translate", json={"file_id": file_id, "target_language": "en"})
    assert response.status_code == 503 and "平台翻译服务" in response.json()["detail"]


@pytest.mark.parametrize("upstream_body,has_error", [
    ("", True),
    ('data: {"choices":[{"delta":{"content":"partial"}}]}\n\n', True),
    ('data: [DONE]\n\n', True),
    ('data: {"choices":[{"delta":{"content":"translation"},"finish_reason":"stop"}]}\n\n', False),
    ('data: {"choices":[{"delta":{"content":"partial"},"finish_reason":"length"}]}\n\n', True),
])
def test_empty_or_interrupted_translation_stream_reports_error(client, app, upstream_body, has_error):
    register(client)
    task_id = upload_task(client).json()["task"]["id"]
    file_id = client.get(f"/api/tasks/{task_id}").json()["files"][0]["id"]
    config = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash", "api_key": "test-secret"}
    assert client.put("/api/settings/translation", json=config).status_code == 200
    app.state.translation_transport = httpx.MockTransport(lambda request: httpx.Response(200, content=upstream_body))
    response = client.post("/api/translate", json={"file_id": file_id, "target_language": "zh"})
    assert response.status_code == 200
    assert ('"error":' in response.text) is has_error
    assert '"done": true' in response.text


def test_text_preview_limits_binary_and_csrf(client):
    register(client)
    entries = {"large.md": ("文档" * 100000).encode(), "binary.bin": b"\x00\x01\x02", "task.toml": b'version="1.0"', "instruction.md": b"Preview tests"}
    task_id = upload_task(client, entries).json()["task"]["id"]
    files = {f["path"]: f for f in client.get(f"/api/tasks/{task_id}").json()["files"]}
    response = client.get(f"/api/files/{files['large.md']['id']}/content")
    assert response.status_code == 200 and response.json()["truncated"]
    assert client.get(f"/api/files/{files['binary.bin']['id']}/content").status_code == 415
    assert client.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/not-real").status_code == 404


def test_chunked_request_is_bounded_before_multipart_parsing(app):
    app.add_middleware(RequestBodyLimit, max_bytes=512)
    with TestClient(app) as client:
        response = client.post("/api/auth/login", content=iter([b"x" * 256 for _ in range(4)]), headers={"Content-Type": "application/json"})
        assert response.status_code == 413
        assert isinstance(response.json()["detail"], str)


def test_seed_is_explicit_demo_idempotent_and_keeps_first_user_admin(tmp_path):
    app = create_app(tmp_path / "demo", seed=True)
    with TestClient(app) as client:
        tasks = client.get("/api/tasks").json()["tasks"]
        assert len(tasks) == 2 and all(task["is_demo"] and task["project_id"] == "project-aa" for task in tasks)
        assert all(task["files_count"] >= 4 and task["rollouts_count"] == 0 and task["reviews_count"] == 0 for task in tasks)
        assert client.get("/api/tasks?project_id=paperbenchx").json()["tasks"] == []
        assert register(client)["role"] == "admin"
    with TestClient(create_app(tmp_path / "demo", seed=True)) as client:
        assert len(client.get("/api/tasks").json()["tasks"]) == 2


def test_initial_projects_filter_tasks_activity_and_derive_counts(client, app):
    empty_projects = client.get("/api/projects").json()["projects"]
    assert empty_projects == [
        {"id": "project-aa", "name": "ProjectAA", "task_sets_count": 0, "tasks_count": 0, "rollouts_count": 0, "reviews_count": 0},
        {"id": "paperbenchx", "name": "PaperBenchX", "task_sets_count": 0, "tasks_count": 0, "rollouts_count": 0, "reviews_count": 0},
    ]
    register(client)
    aa_task = upload_task(client, title="AA first task").json()["task"]
    aa_second = upload_task(client, title="AA second task", project_id="project-aa").json()["task"]
    paper_task = upload_task(client, title="Paper task", project_id="paperbenchx").json()["task"]
    assert aa_task["project_id"] == "project-aa" and paper_task["project_id"] == "paperbenchx"
    assert client.post(f"/api/tasks/{aa_task['id']}/reviews?project_id=project-aa", json={"verdict": "approved", "body": "Reviewed AA criteria"}).status_code == 200
    assert client.post(f"/api/tasks/{paper_task['id']}/rollouts?project_id=paperbenchx", files=[("files", ("result.json", trial_result()))]).status_code == 200
    with TestClient(app) as public:
        expected = {"project-aa": {aa_task["id"], aa_second["id"]}, "paperbenchx": {paper_task["id"]}}
        for project_id, task_ids in expected.items():
            response = public.get("/api/tasks", params={"project_id": project_id})
            assert {task["id"] for task in response.json()["tasks"]} == task_ids
            assert {task["project_id"] for task in response.json()["tasks"]} == {project_id}
            activity = public.get("/api/activity", params={"project_id": project_id}).json()["activity"]
            assert activity and {item["project_id"] for item in activity} == {project_id}
            assert {item["task_id"] for item in activity} <= task_ids
        assert {task["id"] for task in public.get("/api/tasks").json()["tasks"]} == expected["project-aa"]
        assert {item["project_id"] for item in public.get("/api/activity").json()["activity"]} == {"project-aa"}
        projects = {p["id"]: p for p in public.get("/api/projects").json()["projects"]}
        assert (projects["project-aa"]["tasks_count"], projects["project-aa"]["rollouts_count"], projects["project-aa"]["reviews_count"]) == (2, 0, 1)
        assert (projects["paperbenchx"]["tasks_count"], projects["paperbenchx"]["rollouts_count"], projects["paperbenchx"]["reviews_count"]) == (1, 1, 0)


@pytest.mark.parametrize("project_id,other_project", [("project-aa", "paperbenchx"), ("paperbenchx", "project-aa")])
def test_scoped_detail_files_writes_and_translation_reject_other_project(client, app, project_id, other_project):
    register(client)
    task = upload_task(client, project_id=project_id).json()["task"]
    task_id = task["id"]
    correct = {"project_id": project_id}
    wrong = {"project_id": other_project}
    # Existing deep links still identify their task's project without a scope.
    assert client.get(f"/api/tasks/{task_id}").json()["task"]["project_id"] == project_id
    detail = client.get(f"/api/tasks/{task_id}", params=correct).json()
    assert client.get(f"/api/tasks/{task_id}", params=wrong).status_code == 404
    assert client.get(f"/api/tasks/{task_id}", params={"project_id": "missing"}).status_code == 404
    response = client.post(f"/api/tasks/{task_id}/rollouts", params=correct, files=[("files", ("result.json", trial_result()))])
    assert response.status_code == 200, response.text
    rollout_files = response.json()["rollout"]["files"]
    files = detail["files"] + rollout_files
    for file in files:
        for action in ("content", "download"):
            endpoint = f"/api/files/{file['id']}/{action}"
            assert client.get(endpoint, params=correct).status_code == 200
            assert client.get(endpoint).status_code == 200
            assert client.get(endpoint, params=wrong).status_code == 404
            assert client.get(endpoint, params={"project_id": "missing"}).status_code == 404
    for scope in (wrong, {"project_id": "missing"}):
        assert client.post(f"/api/tasks/{task_id}/rollouts", params=scope, files=[("files", ("result.json", trial_result()))]).status_code == 404
        assert client.post(f"/api/tasks/{task_id}/reviews", params=scope, json={"verdict": "approved", "body": "Should not be stored"}).status_code == 404
        # Scope rejection happens even when no translation service is configured.
        assert client.post("/api/translate", json={"file_id": files[0]["id"], "target_language": "zh", **scope}).status_code == 404
    task_after = client.get(f"/api/tasks/{task_id}").json()["task"]
    assert task_after["rollouts_count"] == 1 and task_after["reviews_count"] == 0
    assert client.post(f"/api/tasks/{task_id}/reviews", params=correct, json={"verdict": "approved", "body": "Correct project review"}).status_code == 200
    config = {"provider": "DeepSeek", "base_url": "https://api.deepseek.com", "model": "deepseek-v4-flash", "api_key": "scope-test-key"}
    assert client.put("/api/settings/translation", json=config).status_code == 200
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content='data: {"choices":[{"delta":{"content":"译文"},"finish_reason":"stop"}]}\n\n')
    app.state.translation_transport = httpx.MockTransport(upstream)
    for file in (files[0], rollout_files[0]):
        response = client.post("/api/translate", json={"file_id": file["id"], "target_language": "zh", **correct})
        assert response.status_code == 200 and '"text": "译文"' in response.text
        assert client.post("/api/translate", json={"file_id": file["id"], "target_language": "zh", **wrong}).status_code == 404
    assert len(calls) == 2


def test_unknown_projects_cannot_list_or_accept_uploads(client):
    register(client)
    for endpoint in ("/api/tasks", "/api/activity"):
        assert client.get(endpoint, params={"project_id": "unknown-project"}).status_code == 404
    assert upload_task(client, project_id="unknown-project").status_code == 404
    assert all(project["tasks_count"] == 0 for project in client.get("/api/projects").json()["projects"])


def test_legacy_schema_migrates_in_place_with_relations_sessions_and_files(tmp_path):
    directory = tmp_path / "legacy"
    directory.mkdir()
    blobs = directory / "files"
    blobs.mkdir()
    (blobs / "legacy-file").write_text("# Original task document", encoding="utf-8")
    database = directory / "harbor.sqlite3"
    timestamp = "2026-01-01T00:00:00+00:00"
    token = "legacy-session-token"
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires_at REAL NOT NULL);
            CREATE TABLE tasks (id TEXT PRIMARY KEY,slug TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,category TEXT NOT NULL,difficulty TEXT NOT NULL,tags TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',author_id TEXT,author TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,is_demo INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE rollouts (id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),name TEXT NOT NULL,agent TEXT NOT NULL,model TEXT NOT NULL,status TEXT NOT NULL,reward REAL,duration_seconds REAL,created_at TEXT NOT NULL);
            CREATE TABLE files (id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),rollout_id TEXT REFERENCES rollouts(id),path TEXT NOT NULL,size INTEGER NOT NULL,kind TEXT NOT NULL,mime_type TEXT NOT NULL);
            CREATE TABLE reviews (id TEXT PRIMARY KEY,task_id TEXT NOT NULL REFERENCES tasks(id),author_id TEXT NOT NULL,author TEXT NOT NULL,verdict TEXT NOT NULL,body TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE activity (id TEXT PRIMARY KEY,type TEXT NOT NULL,author TEXT NOT NULL,task_id TEXT NOT NULL REFERENCES tasks(id),description TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE settings (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        """)
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", ("legacy-user", "Legacy owner", "legacy@example.com", password_hash("legacy-secure-password"), "admin", timestamp))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", (hashlib.sha256(token.encode()).hexdigest(), "legacy-user", 4102444800))
        db.execute("INSERT INTO tasks (rowid,id,slug,title,description,category,difficulty,tags,status,author_id,author,created_at,updated_at,is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (41, "legacy-task", "legacy-task", "Existing task", "Existing description", "software-engineering", "medium", '["legacy"]', "approved", "legacy-user", "Legacy owner", timestamp, timestamp, 0))
        db.execute("INSERT INTO rollouts VALUES (?,?,?,?,?,?,?,?,?)", ("legacy-rollout", "legacy-task", "Existing trial", "codex", "existing-model", "passed", 1, 15, timestamp))
        db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?)", ("legacy-file", "legacy-task", None, "instruction.md", 24, "task", "text/plain"))
        db.execute("INSERT INTO reviews (rowid,id,task_id,author_id,author,verdict,body,created_at) VALUES (?,?,?,?,?,?,?,?)", (77, "legacy-review", "legacy-task", "legacy-user", "Legacy owner", "approved", "Existing review", timestamp))
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?)", ("legacy-activity", "upload", "Legacy owner", "legacy-task", "Existing upload", timestamp))
        db.execute("INSERT INTO settings VALUES ('seed_initialized','true')")
    app = create_app(directory, seed=False)
    with TestClient(app) as client:
        client.cookies.set("harbor_session", token)
        assert client.get("/api/auth/me").json()["user"]["id"] == "legacy-user"
        assert client.get("/api/auth/me").json()["user"]["username"] is None
        assert "external_id" not in client.get("/api/auth/me").text
        client.post("/api/auth/logout")
        login = client.post("/api/auth/login", json={"email": " LEGACY@EXAMPLE.COM ", "password": "legacy-secure-password"})
        assert login.status_code == 200 and login.json()["user"]["id"] == "legacy-user"
        detail = client.get("/api/tasks/legacy-task?project_id=project-aa").json()
        assert detail["task"]["project_id"] == "project-aa"
        assert detail["task"]["task_set_id"] == "default:project-aa"
        assert detail["task"]["task_set_name"] == "默认任务集"
        assert client.get("/api/task-sets?project_id=project-aa").json()["task_sets"][0]["tasks_count"] == 1
        assert client.get("/api/task-sets?project_id=paperbenchx").json()["task_sets"] == []
        assert (detail["task"]["files_count"], detail["task"]["rollouts_count"], detail["task"]["reviews_count"]) == (1, 1, 1)
        assert detail["reviews"][0]["body"] == "Existing review"
        assert client.get("/api/files/legacy-file/content?project_id=project-aa").json()["content"] == "# Original task document"
        assert client.get("/api/tasks?project_id=paperbenchx").json()["tasks"] == []
        assert client.get("/api/files/legacy-file/content?project_id=paperbenchx").status_code == 404
        assert client.get("/api/activity").json()["activity"][0]["project_id"] == "project-aa"
        assert upload_task(client, project_id="paperbenchx").status_code == 200
    with app.state.store.connect() as db:
        legacy_user = db.execute("SELECT rowid,username,external_id FROM users WHERE id='legacy-user'").fetchone()
        assert legacy_user["rowid"] == 1 and legacy_user["username"] is None and legacy_user["external_id"] is None
        assert db.execute("SELECT rowid FROM tasks WHERE id='legacy-task'").fetchone()[0] == 41
        assert db.execute("SELECT rowid FROM reviews WHERE id='legacy-review'").fetchone()[0] == 77
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
        assert next(row for row in db.execute("PRAGMA table_info(tasks)") if row["name"] == "project_id")["notnull"] == 1
        assert any(row["table"] == "projects" and row["from"] == "project_id" for row in db.execute("PRAGMA foreign_key_list(tasks)"))
        assert any(row["table"] == "task_sets" and row["from"] == "task_set_id" for row in db.execute("PRAGMA foreign_key_list(tasks)"))
    with pytest.raises(sqlite3.IntegrityError):
        with app.state.store.connect() as db:
            db.execute("UPDATE tasks SET project_id='unknown' WHERE id='legacy-task'")
    with TestClient(create_app(directory, seed=False)) as reopened:
        assert len(reopened.get("/api/tasks").json()["tasks"]) == 1
        assert len(reopened.get("/api/tasks?project_id=paperbenchx").json()["tasks"]) == 1


def test_tag_filter_and_vocabulary_reflect_uploaded_tags(client):
    register(client)
    physics = upload_task(client, title="Physics task")
    both = upload_task(client, title="Physics and AI task")
    for task_id, tags in ((physics.json()["task"]["id"], ["物理"]), (both.json()["task"]["id"], ["物理", "人工智能"])):
        with client.app.state.store.connect() as db:
            db.execute("UPDATE tasks SET tags=? WHERE id=?", (json.dumps(tags, ensure_ascii=False), task_id))

    def ids(**params):
        response = client.get("/api/tasks", params=params)
        assert response.status_code == 200, response.text
        return {task["id"] for task in response.json()["tasks"]}

    assert ids() == {physics.json()["task"]["id"], both.json()["task"]["id"]}
    assert ids(tag="物理") == {physics.json()["task"]["id"], both.json()["task"]["id"]}
    assert ids(tag="人工智能") == {both.json()["task"]["id"]}
    # Repeating the parameter intersects rather than unions.
    assert ids(tag=["物理", "人工智能"]) == {both.json()["task"]["id"]}
    assert ids(tag="化学") == set()
    # A blank value is ignored instead of matching nothing.
    assert ids(tag=" ") == {physics.json()["task"]["id"], both.json()["task"]["id"]}

    vocabulary = client.get("/api/tags").json()
    assert vocabulary["preset"] == ["物理", "化学", "生物", "医学", "人工智能", "具身智能", "编程"]
    assert vocabulary["in_use"] == [{"tag": "物理", "count": 2}, {"tag": "人工智能", "count": 1}]


def test_upload_accepts_custom_tags_and_rejects_oversized_ones(client):
    register(client)
    entries = {"task/instruction.md": b"# Task", "task/task.toml": b'version = "1.0"\n'}
    files = [("files", (path.rsplit("/", 1)[-1], content, "application/octet-stream")) for path, content in entries.items()]
    base = {"paths": json.dumps(list(entries))}

    accepted = client.post("/api/tasks", data={**base, "tags": json.dumps(["具身智能", "自定义标签"], ensure_ascii=False)}, files=files)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["task"]["tags"] == ["具身智能", "自定义标签"]

    assert client.post("/api/tasks", data={**base, "tags": json.dumps(["x" * 51])}, files=files).status_code == 422
    assert client.post("/api/tasks", data={**base, "tags": json.dumps([str(n) for n in range(21)])}, files=files).status_code == 422
    assert client.post("/api/tasks", data={**base, "tags": "物理"}, files=files).status_code == 422


def test_omitted_tags_keep_the_manifest_tags_and_an_empty_list_clears_them(client):
    register(client)
    manifest = b'version = "1.0"\n\n[metadata]\ntags = ["\xe7\x89\xa9\xe7\x90\x86"]\n'
    entries = {"task/instruction.md": b"# Task", "task/task.toml": manifest}
    files = [("files", (path.rsplit("/", 1)[-1], content, "application/octet-stream")) for path, content in entries.items()]
    base = {"paths": json.dumps(list(entries))}

    inherited = client.post("/api/tasks", data=base, files=files)
    assert inherited.status_code == 200, inherited.text
    assert inherited.json()["task"]["tags"] == ["物理"]

    cleared = client.post("/api/tasks", data={**base, "tags": "[]"}, files=files)
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["task"]["tags"] == []
