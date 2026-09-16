import hashlib
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

_module_data = tempfile.TemporaryDirectory(prefix="harbor-task-set-tests-")
os.environ["HARBOR_DATA_DIR"] = _module_data.name
os.environ["HARBOR_SEED_DEMO"] = "false"
from server.main import create_app  # noqa: E402
from server.archiving import payload_digest  # noqa: E402


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("HARBOR_PUBLIC_URL", raising=False)
    return create_app(tmp_path / "data", seed=False)


@pytest.fixture
def client(app):
    with TestClient(app) as connection:
        yield connection


def register(client, email="owner@example.test"):
    response = client.post("/api/auth/register", json={"name": email.split("@")[0], "email": email, "password": "test-password-long"})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def project(client, name="Research project"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 200, response.text
    return response.json()["project"]


def task_set(client, project_id="project-aa", name="Evaluation batch"):
    response = client.post("/api/task-sets", json={"name": name, "project_id": project_id})
    assert response.status_code == 200, response.text
    return response.json()["task_set"]


def token(client, project_id):
    response = client.post("/api/auth/tokens", json={"name": "Task set tests", "project_id": project_id})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def auth(secret, key=None):
    return {"Authorization": "Bearer " + secret, **({"Idempotency-Key": key} if key else {})}


TASK_FILES = {"task.toml": b'version="1.0"', "instruction.md": b"# Stored instructions"}


def upload(client, project_id="project-aa", task_set_id=None, *, secret=None, key=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for path, content in TASK_FILES.items():
            archive.writestr(path, content)
    data = {"project_id": project_id}
    if task_set_id is not None:
        data["task_set_id"] = task_set_id
    return client.post("/api/v1/tasks" if secret else "/api/tasks", data=data,
                       headers=auth(secret, key) if secret else {},
                       files=[("files", ("stored-task.zip", stream.getvalue(), "application/zip"))])


def append(client, task_id, *, secret=None, key=None, **scope):
    return client.post(f"/api/{'v1/' if secret else ''}tasks/{task_id}/rollouts",
                       data=scope if secret else {}, params=scope if not secret else {},
                       headers=auth(secret, key) if secret else {},
                       files=[("files", ("result.json", b'{"trial_name":"stored-run","agent_info":{"name":"agent"},"verifier_result":{"rewards":{"reward":1}}}'))])


def test_members_can_create_dynamic_projects_task_sets_and_upload_while_public_is_read_only(client, app):
    assert client.post("/api/projects", json={"name": "Unauthorized"}).status_code == 401
    assert client.post("/api/task-sets", json={"project_id": "project-aa", "name": "Unauthorized"}).status_code == 401
    register(client)
    member = register(client, "member@example.test")
    assert member["role"] == "member"
    created = project(client, "  Research workspace  ")
    assert created["name"] == "Research workspace"
    assert all(created[key] == 0 for key in ("task_sets_count", "tasks_count", "rollouts_count", "reviews_count"))
    assert client.get("/api/task-sets", params={"project_id": created["id"]}).json() == {"task_sets": []}
    group = task_set(client, created["id"], "  September batch  ")
    assert group["name"] == "September batch" and group["author"] == member["name"]
    assert all(group[key] == 0 for key in ("tasks_count", "rollouts_count", "pending_count", "approved_count"))
    task = upload(client, created["id"], group["id"]).json()["task"]
    assert task["task_set_id"] == group["id"] and task["task_set_name"] == group["name"]
    assert task["author"] == member["name"]
    with TestClient(app) as public:
        assert public.get("/api/task-sets/" + group["id"]).json()["task_set"]["tasks_count"] == 1
        assert public.get("/api/tasks", params={"project_id": created["id"], "task_set_id": group["id"]}).json()["tasks"][0]["id"] == task["id"]
        assert upload(public, created["id"], group["id"]).status_code == 401
    projects = {item["id"]: item for item in client.get("/api/projects").json()["projects"]}
    assert projects[created["id"]]["task_sets_count"] == 1 and projects[created["id"]]["tasks_count"] == 1


@pytest.mark.parametrize("name", ["", "  ", "a" * 81])
def test_project_and_task_set_names_are_validated_atomically(client, name):
    register(client)
    assert client.post("/api/projects", json={"name": name}).status_code == 422
    assert client.post("/api/task-sets", json={"project_id": "project-aa", "name": name}).status_code == 422
    assert len(client.get("/api/projects").json()["projects"]) == 2
    assert client.get("/api/task-sets").json()["task_sets"] == []


def test_task_set_scopes_filter_counts_and_reject_cross_set_reads_writes_and_translation(client):
    register(client)
    first, second = task_set(client, name="First batch"), task_set(client, name="Second batch")
    foreign = task_set(client, "paperbenchx", "Foreign batch")
    task_one = upload(client, task_set_id=first["id"]).json()["task"]
    task_two = upload(client, task_set_id=second["id"]).json()["task"]
    assert upload(client, task_set_id=foreign["id"]).status_code == 404
    assert upload(client, task_set_id="missing").status_code == 404
    assert client.post("/api/task-sets", json={"project_id": "missing", "name": "No project"}).status_code == 404
    assert append(client, task_one["id"], task_set_id=first["id"]).status_code == 200
    assert client.post(f"/api/tasks/{task_one['id']}/reviews", params={"task_set_id": first["id"]}, json={"verdict": "approved", "body": "Reviewed this set"}).status_code == 200
    groups = {item["id"]: item for item in client.get("/api/task-sets").json()["task_sets"]}
    assert (groups[first["id"]]["tasks_count"], groups[first["id"]]["rollouts_count"], groups[first["id"]]["pending_count"], groups[first["id"]]["approved_count"]) == (1, 1, 0, 1)
    assert (groups[second["id"]]["tasks_count"], groups[second["id"]]["pending_count"], groups[second["id"]]["approved_count"]) == (1, 1, 0)
    for group, task in ((first, task_one), (second, task_two)):
        scope = {"project_id": "project-aa", "task_set_id": group["id"]}
        assert [row["id"] for row in client.get("/api/tasks", params=scope).json()["tasks"]] == [task["id"]]
        events = client.get("/api/activity", params=scope).json()["activity"]
        assert events and all(event["task_set_id"] == group["id"] and event["task_id"] == task["id"] for event in events)
    detail = client.get(f"/api/tasks/{task_one['id']}").json()
    all_files = detail["files"] + detail["rollouts"][0]["files"]
    for wrong in (second["id"], foreign["id"], "missing"):
        scope = {"task_set_id": wrong}
        assert client.get(f"/api/tasks/{task_one['id']}", params=scope).status_code == 404
        assert append(client, task_one["id"], **scope).status_code == 404
        assert client.post(f"/api/tasks/{task_one['id']}/reviews", params=scope, json={"verdict": "approved", "body": "Must fail"}).status_code == 404
        for file in all_files:
            for action in ("content", "download"):
                assert client.get(f"/api/files/{file['id']}/{action}", params=scope).status_code == 404
            assert client.post("/api/translate", json={"file_id": file["id"], "target_language": "zh", **scope}).status_code == 404
    for path in ("/api/tasks", "/api/activity"):
        assert client.get(path, params={"project_id": "project-aa", "task_set_id": foreign["id"]}).status_code == 404
    assert client.get("/api/task-sets/" + first["id"], params={"project_id": "paperbenchx"}).status_code == 404
    assert client.get("/api/task-sets/missing").status_code == 404
    assert client.get(f"/api/tasks/{task_one['id']}").json()["task"]["reviews_count"] == 1


def test_legacy_uploads_create_default_set_lazily_without_changing_explicit_sets(client, app):
    register(client)
    assert client.get("/api/task-sets").json()["task_sets"] == []
    custom = task_set(client)
    explicit = upload(client, task_set_id=custom["id"]).json()["task"]
    first, second = upload(client).json()["task"], upload(client).json()["task"]
    assert first["task_set_id"] == second["task_set_id"] == "default:project-aa"
    assert first["task_set_id"] != explicit["task_set_id"]
    assert first["task_set_name"] == "默认任务集"
    assert client.get("/api/task-sets", params={"project_id": "paperbenchx"}).json()["task_sets"] == []
    with TestClient(create_app(app.state.store.root, seed=False)) as reopened:
        assert len(reopened.get("/api/task-sets").json()["task_sets"]) == 2
        assert len(reopened.get("/api/tasks").json()["tasks"]) == 3


def test_agent_task_set_creation_scope_and_idempotent_uploads(client, app):
    register(client)
    member = register(client, "member@example.test")
    dynamic = project(client)
    secret = token(client, dynamic["id"])
    foreign = task_set(client, "paperbenchx")
    response = client.post("/api/v1/task-sets", headers=auth(secret), json={"name": "  Agent batch  "})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    first = response.json()["task_set"]
    second = task_set(client, dynamic["id"], "Second batch")
    assert first["name"] == "Agent batch" and first["author"] == member["name"]
    assert {row["id"] for row in client.get("/api/v1/task-sets", headers=auth(secret)).json()["task_sets"]} == {first["id"], second["id"]}
    assert client.get("/api/v1/task-sets", headers=auth(secret), params={"project_id": "paperbenchx"}).status_code == 404
    assert client.post("/api/v1/task-sets", headers=auth(secret), json={"name": "Forbidden", "project_id": "paperbenchx"}).status_code == 404
    assert client.post("/api/v1/task-sets", json={"name": "Cookie cannot authorize"}).status_code == 401
    for name in (" ", "x" * 81):
        assert client.post("/api/v1/task-sets", headers=auth(secret), json={"name": name}).status_code == 422
    first_response = upload(client, dynamic["id"], first["id"], secret=secret, key="same-key")
    assert first_response.status_code == 200, first_response.text
    task = first_response.json()["task"]
    replay = upload(client, dynamic["id"], first["id"], secret=secret, key="same-key").json()
    assert replay["replayed"] and replay["task"]["id"] == task["id"]
    assert upload(client, dynamic["id"], second["id"], secret=secret, key="same-key").status_code == 409
    assert upload(client, dynamic["id"], foreign["id"], secret=secret, key="same-key").status_code == 404
    assert parse_qs(urlparse(replay["task_url"]).query)["task_set"] == [first["id"]]
    assert parse_qs(urlparse(replay["api_url"]).query)["task_set_id"] == [first["id"]]
    assert client.get(f"/api/v1/tasks/{task['id']}", headers=auth(secret)).json()["task"]["task_set_name"] == first["name"]
    for wrong in (second["id"], foreign["id"], "missing"):
        assert client.get(f"/api/v1/tasks/{task['id']}", headers=auth(secret), params={"task_set_id": wrong}).status_code == 404
        assert append(client, task["id"], secret=secret, task_set_id=wrong).status_code == 404
    assert client.get("/api/v1/tasks", headers=auth(secret), params={"task_set_id": second["id"]}).json() == {"tasks": []}
    assert client.get("/api/v1/tasks", headers=auth(secret), params={"task_set_id": foreign["id"]}).status_code == 404
    result = append(client, task["id"], secret=secret, key="append-key").json()
    repeated = append(client, task["id"], secret=secret, key="append-key", task_set_id=first["id"]).json()
    assert repeated["replayed"] and repeated["rollout"]["id"] == result["rollout"]["id"]
    assert parse_qs(urlparse(result["task_url"]).query)["task_set"] == [first["id"]]
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM activity WHERE task_id=?", (task["id"],)).fetchone()[0] == 2
    with TestClient(app) as other:
        register(other, "other@example.test")
        others_token = token(other, dynamic["id"])
        assert append(other, task["id"], secret=others_token, task_set_id=first["id"]).status_code == 403
        assert upload(other, dynamic["id"], first["id"], secret=others_token).status_code == 200


def test_concurrent_legacy_agent_uploads_create_one_default_set_and_one_task(client, app):
    register(client)
    secret = token(client, "paperbenchx")
    barrier = Barrier(2)
    def send(_):
        with TestClient(app) as connection:
            barrier.wait(timeout=10)
            return upload(connection, "paperbenchx", secret=secret, key="default-concurrent")
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, range(2)))
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted(response.json()["replayed"] for response in responses) == [False, True]
    groups = client.get("/api/task-sets?project_id=paperbenchx").json()["task_sets"]
    assert len(groups) == 1 and groups[0]["tasks_count"] == 1
    replay = upload(client, "paperbenchx", groups[0]["id"], secret=secret, key="default-concurrent")
    assert replay.status_code == 200 and replay.json()["replayed"]


def test_pre_task_set_idempotency_responses_upgrade_without_duplicate_uploads(client, app):
    register(client)
    secret = token(client, "project-aa")
    original = upload(client, secret=secret, key="old-client-key").json()
    task = original["task"]
    fields = {key: task[key] for key in ("title", "description", "category", "difficulty", "tags")}
    old_hash = payload_digest({"files": TASK_FILES, "fields": fields}, "project-aa")
    original["task"].pop("task_set_id")
    original["task"].pop("task_set_name")
    original["task_url"] = "http://testserver/?project=project-aa&task=" + task["id"]
    original["api_url"] = "http://testserver/api/v1/tasks/" + task["id"]
    with app.state.store.connect() as db:
        db.execute("UPDATE api_idempotency SET payload_hash=?,response_json=? WHERE idempotency_key='old-client-key'", (old_hash, json.dumps(original)))
    wrong = task_set(client)
    assert upload(client, task_set_id=wrong["id"], secret=secret, key="old-client-key").status_code == 409
    response = upload(client, secret=secret, key="old-client-key")
    assert response.status_code == 200 and response.json()["replayed"]
    assert response.json()["task"]["task_set_name"] == "默认任务集"
    assert parse_qs(urlparse(response.json()["task_url"]).query)["task_set"] == ["default:project-aa"]
    assert len(client.get("/api/tasks").json()["tasks"]) == 1


def test_task_set_migration_preserves_custom_projects_rowids_and_database_integrity(tmp_path):
    directory = tmp_path / "legacy-project-schema"
    directory.mkdir()
    timestamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(directory / "harbor.sqlite3") as db:
        db.executescript("""
            CREATE TABLE projects (id TEXT PRIMARY KEY,name TEXT NOT NULL);
            CREATE TABLE tasks (id TEXT PRIMARY KEY,slug TEXT NOT NULL,title TEXT NOT NULL,description TEXT NOT NULL,category TEXT NOT NULL,difficulty TEXT NOT NULL,tags TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending',author_id TEXT,author TEXT NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,is_demo INTEGER NOT NULL DEFAULT 0,project_id TEXT NOT NULL DEFAULT 'project-aa' REFERENCES projects(id));
        """)
        db.executemany("INSERT INTO projects VALUES (?,?)", [("project-aa", "Existing custom title"), ("paperbenchx", "PaperBenchX"), ("custom-old", "An existing dynamic project")])
        for rowid, project_id in ((41, "project-aa"), (57, "custom-old")):
            db.execute("INSERT INTO tasks (rowid,id,slug,title,description,category,difficulty,tags,author,created_at,updated_at,project_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (rowid, f"old-{rowid}", f"old-{rowid}", "Original", "Original description", "software-engineering", "medium", "[]", "Original owner", timestamp, timestamp, project_id))
    app = create_app(directory, seed=False)
    with TestClient(app) as connection:
        projects = {item["id"]: item for item in connection.get("/api/projects").json()["projects"]}
        assert projects["project-aa"]["name"] == "Existing custom title"
        assert projects["custom-old"]["tasks_count"] == projects["custom-old"]["task_sets_count"] == 1
        assert projects["paperbenchx"]["task_sets_count"] == 0
        assert connection.get("/api/tasks/old-57").json()["task"]["task_set_id"] == "default:custom-old"
    with app.state.store.connect() as db:
        assert [(row["rowid"], row["task_set_id"]) for row in db.execute("SELECT rowid,task_set_id FROM tasks ORDER BY rowid")] == [(41, "default:project-aa"), (57, "default:custom-old")]
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
    for sql in (
        "UPDATE tasks SET task_set_id='default:custom-old' WHERE id='old-41'",
        "UPDATE tasks SET project_id='custom-old' WHERE id='old-41'",
        "UPDATE tasks SET task_set_id=NULL WHERE id='old-41'",
        "UPDATE task_sets SET project_id='paperbenchx' WHERE id='default:project-aa'",
    ):
        with pytest.raises(sqlite3.IntegrityError), app.state.store.connect() as db:
            db.execute(sql)
    reopened = create_app(directory, seed=False)
    with reopened.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM task_sets").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 2


def test_seed_and_legacy_sql_inserts_are_assigned_to_a_task_set(tmp_path):
    app = create_app(tmp_path / "seed", seed=True)
    with app.state.store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM tasks WHERE task_set_id IS NULL").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM task_sets WHERE project_id='project-aa'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM task_sets WHERE project_id='paperbenchx'").fetchone()[0] == 0
        db.execute("""INSERT INTO tasks (id,slug,title,description,category,difficulty,tags,author,created_at,updated_at,project_id)
            VALUES ('legacy-writer','legacy-writer','Stored','','software-engineering','medium','[]','System','2026-09-16','2026-09-16','paperbenchx')""")
        assert db.execute("SELECT task_set_id FROM tasks WHERE id='legacy-writer'").fetchone()[0] == "default:paperbenchx"
        assert not db.execute("PRAGMA foreign_key_check").fetchall()
