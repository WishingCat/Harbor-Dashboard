import sqlite3

import pytest

from server.auth import password_hash, password_matches
from server.deployment import export_snapshot, initialize_data
from server.seed import seed_demo
from server.storage import Store


@pytest.fixture
def snapshot(tmp_path):
    source = Store(tmp_path / "original")
    seed_demo(source)
    with source.connect() as db:
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", (
            "test-user", "测试用户", "test@example.invalid", password_hash("ceshi"),
            "admin", "2026-09-16", "ceshi", None,
        ))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", ("old-browser-session", "test-user", 9999999999))
    destination = tmp_path / "snapshot"
    export_snapshot(source.root, destination)
    return source, destination


def test_export_preserves_accounts_files_and_source_but_drops_sessions(snapshot, tmp_path):
    source, bundled = snapshot
    assert initialize_data(tmp_path / "deployed", bundled)
    restored = Store(tmp_path / "deployed")
    with restored.connect() as db, source.connect() as original:
        user = db.execute("SELECT * FROM users").fetchone()
        assert user["username"] == "ceshi" and user["role"] == "admin"
        assert password_matches("ceshi", user["password_hash"])
        assert not password_matches("wrong", user["password_hash"])
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert original.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        for table in ("users", "projects", "task_sets", "tasks", "files", "settings"):
            assert [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")] == [
                tuple(row) for row in original.execute(f"SELECT * FROM {table} ORDER BY rowid")
            ]
        for row in db.execute("SELECT id FROM files"):
            assert (restored.blobs / row[0]).read_bytes() == (source.blobs / row[0]).read_bytes()


def test_restart_keeps_deployment_changes_even_without_snapshot(snapshot, tmp_path):
    _, bundled = snapshot
    target = tmp_path / "deployed"
    initialize_data(target, bundled)
    with Store(target).connect() as db:
        db.execute("UPDATE users SET password_hash=?", (password_hash("changed-password"),))
        db.execute("INSERT INTO projects VALUES ('new-project','Created after deployment')")
    assert not initialize_data(target, tmp_path / "missing-snapshot")
    with Store(target).connect() as db:
        assert password_matches("changed-password", db.execute("SELECT password_hash FROM users").fetchone()[0])
        assert db.execute("SELECT COUNT(*) FROM projects").fetchone()[0] == 3


def test_incomplete_snapshot_fails_without_publishing_database(snapshot, tmp_path):
    _, bundled = snapshot
    next((bundled / "files").iterdir()).unlink()
    target = tmp_path / "deployed"
    with pytest.raises(ValueError, match="missing or incomplete"):
        initialize_data(target, bundled)
    assert not (target / "harbor.sqlite3").exists()


def test_missing_snapshot_fails_instead_of_creating_empty_workspace(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        initialize_data(tmp_path / "deployed", tmp_path / "missing")
    assert not (tmp_path / "deployed" / "harbor.sqlite3").exists()


def test_bootstrapped_app_can_login_and_restart(snapshot, tmp_path, monkeypatch):
    _, bundled = snapshot
    monkeypatch.setenv("HARBOR_DATA_DIR", str(tmp_path / "module-data"))
    monkeypatch.setenv("HARBOR_SEED_DEMO", "false")
    from fastapi.testclient import TestClient
    from server.main import create_app
    target = tmp_path / "deployed"
    with TestClient(create_app(target, seed=False, bootstrap_dir=bundled)) as client:
        response = client.post("/api/auth/login", json={"username": "ceshi", "password": "ceshi"})
        assert response.status_code == 200
        assert response.json()["user"]["role"] == "admin"
        cookie = client.cookies.get("harbor_session")
    with TestClient(create_app(target, seed=False, bootstrap_dir=bundled)) as client:
        client.cookies.set("harbor_session", cookie)
        assert client.get("/api/auth/me").json()["user"]["username"] == "ceshi"
