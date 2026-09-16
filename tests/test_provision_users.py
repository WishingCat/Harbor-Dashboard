import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from server import provision_users
from server.auth import password_hash, password_matches
from server.storage import Store, uid


SOURCE = "verified-roster-test"


def write_csv(tmp_path, rows, headers=("name", "username")):
    path = tmp_path / "members.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    return path


def invoke(path, directory, capsys, *extra):
    code = provision_users.main(["--input", str(path), "--source", SOURCE, "--data-dir", str(directory), *extra])
    captured = capsys.readouterr()
    assert captured.err == ""
    return code, json.loads(captured.out), captured.out


def accounts(store):
    with store.connect() as db:
        return [dict(row) for row in db.execute("SELECT * FROM users ORDER BY id")]


def existing_account(store, *, name="保留原用户", username=None, external_id=None, email="existing@example.com"):
    user_id = uid()
    with store.connect() as db:
        db.execute("INSERT INTO users (id,name,email,password_hash,role,created_at,username,external_id) VALUES (?,?,?,?,?,?,?,?)", (
            user_id, name, email, password_hash("existing-account-password"), "admin", "2026-01-01T00:00:00Z", username, external_id,
        ))
    return user_id


def test_default_dry_run_does_not_create_database_or_generate_password_hashes(tmp_path, capsys, monkeypatch):
    csv_path = write_csv(tmp_path, [("测试成员甲", "ceshijia"), ("测试成员乙", "yi")])
    directory = tmp_path / "not-created"
    def forbidden_hash(value):
        raise AssertionError("Dry run must not hash passwords")
    monkeypatch.setattr(provision_users, "password_hash", forbidden_hash)
    code, result, output = invoke(csv_path, directory, capsys)
    assert code == 0 and result["mode"] == "dry-run" and result["applied"] is False
    assert (result["create"], result["skip"], result["total"]) == (2, 0, 2)
    assert not directory.exists()
    assert "ceshijia" not in output and '"yi"' not in output
    assert "password" not in output and "scrypt$" not in output and "accounts.invalid" not in output


def test_apply_creates_stable_local_identities_short_passwords_and_only_explicit_admin(tmp_path, capsys):
    csv_path = write_csv(tmp_path, [("测试成员甲", "ceshijia"), ("测试成员乙", "yi")])
    directory = tmp_path / "data"
    code, result, output = invoke(csv_path, directory, capsys, "--apply", "--admin-username", "yi")
    assert code == 0 and result["applied"] is True and result["create"] == 2
    store = Store(directory)
    users = {user["name"]: user for user in accounts(store)}
    for name, username in (("测试成员甲", "ceshijia"), ("测试成员乙", "yi")):
        user = users[name]
        assert user["username"] == username
        assert password_matches(username, user["password_hash"])
        assert user["password_hash"] != username and not password_matches("wrong", user["password_hash"])
        assert user["external_id"] == f"imported:{SOURCE}:{hashlib.sha256(name.encode()).hexdigest()}"
        assert user["email"] == hashlib.sha256(user["external_id"].encode()).hexdigest() + "@accounts.invalid"
        assert user["role"] == ("admin" if username == "yi" else "member")
        assert user["password_hash"] not in output and user["email"] not in output
    assert "ceshijia" not in output and '"yi"' not in output and "password_hash" not in output


def test_repeat_import_skips_and_preserves_password_roles_ids_and_sessions(tmp_path, capsys):
    csv_path = write_csv(tmp_path, [("测试成员甲", "ceshijia"), ("测试成员乙", "yi")])
    directory = tmp_path / "data"
    assert invoke(csv_path, directory, capsys, "--apply")[0] == 0
    store = Store(directory)
    user_id = accounts(store)[0]["id"]
    with store.connect() as db:
        db.execute("UPDATE users SET password_hash=?,role='admin' WHERE id=?", (password_hash("changed-by-user"), user_id))
        db.execute("INSERT INTO sessions (token_hash,user_id,expires_at) VALUES (?,?,?)", ("existing-session", user_id, 4102444800))
    before = accounts(store)
    code, result, output = invoke(csv_path, directory, capsys, "--apply", "--admin-username", "ceshijia")
    assert code == 0 and (result["create"], result["skip"]) == (0, 2)
    assert accounts(store) == before
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions WHERE token_hash='existing-session'").fetchone()[0] == 1
    assert "changed-by-user" not in output and "ceshijia" not in output


@pytest.mark.parametrize("kind,expected", [
    ("username", "username_collision"), ("name", "name_collision"),
    ("external", "existing_identity_mismatch"), ("changed_username", "existing_identity_mismatch"),
    ("email", "reserved_email_collision"),
])
def test_existing_account_conflicts_reject_entire_batch_without_writes(tmp_path, capsys, kind, expected):
    directory = tmp_path / "data"
    store = Store(directory)
    local_id, reserved_email = provision_users.local_identity(SOURCE, "冲突成员")
    kwargs = {
        "username": {"username": "CHONGTU"},
        "name": {"name": "冲突成员"},
        "external": {"external_id": local_id},
        "changed_username": {"name": "冲突成员", "username": "previous", "external_id": local_id},
        "email": {"email": reserved_email},
    }[kind]
    existing_account(store, **kwargs)
    before = accounts(store)
    csv_path = write_csv(tmp_path, [("本可创建的成员", "keyichuangjian"), ("冲突成员", "chongtu")])
    code, result, output = invoke(csv_path, directory, capsys, "--apply")
    assert code == 2 and result["applied"] is False
    assert expected in {item["code"] for item in result["conflicts"]}
    assert accounts(store) == before
    assert "chongtu" not in output and "keyichuangjian" not in output and "scrypt$" not in output


@pytest.mark.parametrize("rows,expected", [
    ([("成员甲", "same"), ("成员乙", "same")], "duplicate_username"),
    ([("成员甲", "first"), ("成员甲", "second")], "duplicate_name"),
    ([("Person", "first"), ("person", "second")], "duplicate_name"),
    ([("成员甲", "Upper")], "invalid_username"),
    ([("成员甲", " spaced ")], "invalid_username"),
    ([("成员甲", "中文")], "invalid_username"),
    ([("成员甲", "name-with-dash")], "invalid_username"),
    ([("成员甲", "a" * 129)], "invalid_username"),
    ([("成员甲", "")], "invalid_username"),
    ([("", "valid")], "invalid_name"),
])
def test_invalid_or_duplicate_csv_rejects_without_creating_target(tmp_path, capsys, rows, expected):
    path = write_csv(tmp_path, rows)
    directory = tmp_path / "untouched"
    code, result, _ = invoke(path, directory, capsys, "--apply")
    assert code == 2 and expected in {item["code"] for item in result["conflicts"]}
    assert not directory.exists()


def test_legacy_accounts_and_schema_are_unchanged_in_dry_run_then_preserved_on_apply(tmp_path, capsys):
    directory = tmp_path / "legacy"
    directory.mkdir()
    database = directory / "harbor.sqlite3"
    encoded = password_hash("legacy-password")
    with sqlite3.connect(database) as db:
        db.executescript("""
            CREATE TABLE users (id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT UNIQUE NOT NULL,password_hash TEXT NOT NULL,role TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),expires_at REAL NOT NULL);
        """)
        db.execute("INSERT INTO users VALUES (?,?,?,?,?,?)", ("legacy-id", "已有成员", "legacy@example.com", encoded, "admin", "2026-01-01"))
        db.execute("INSERT INTO sessions VALUES (?,?,?)", ("legacy-token", "legacy-id", 4102444800))
    path = write_csv(tmp_path, [("新增测试成员", "xin")])
    before_bytes = database.read_bytes()
    assert invoke(path, directory, capsys)[0] == 0
    assert database.read_bytes() == before_bytes
    with sqlite3.connect(database) as db:
        assert "username" not in {row[1] for row in db.execute("PRAGMA table_info(users)")}
    assert invoke(path, directory, capsys, "--apply")[0] == 0
    store = Store(directory)
    with store.connect() as db:
        old = dict(db.execute("SELECT * FROM users WHERE id='legacy-id'").fetchone())
        assert old["password_hash"] == encoded and old["role"] == "admin" and old["created_at"] == "2026-01-01"
        assert old["username"] is None and old["external_id"] is None
        assert db.execute("SELECT user_id FROM sessions WHERE token_hash='legacy-token'").fetchone()[0] == "legacy-id"
        assert db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 2


def test_write_failure_rolls_back_every_new_account_and_redacts_error_details(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "data"
    store = Store(directory)
    existing_account(store)
    before = accounts(store)
    path = write_csv(tmp_path, [("成员甲", "firstsecret"), ("成员乙", "secondsecret")])
    real_hash = provision_users.password_hash
    calls = 0
    def fail_second(value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("private failure detail secondsecret")
        return real_hash(value)
    monkeypatch.setattr(provision_users, "password_hash", fail_second)
    code, result, output = invoke(path, directory, capsys, "--apply")
    assert code == 1 and result["applied"] is False
    assert result["conflicts"][0]["code"] == "database_error"
    assert accounts(store) == before
    assert "firstsecret" not in output and "secondsecret" not in output and "private failure" not in output


def test_concurrent_registration_is_rechecked_under_transaction_lock(tmp_path, capsys, monkeypatch):
    directory = tmp_path / "data"
    store = Store(directory)
    path = write_csv(tmp_path, [("成员甲", "newmember"), ("成员乙", "race")])
    real_read = provision_users.read_existing
    def register_during_preflight(target):
        previous = real_read(target)
        existing_account(store, username="race")
        return previous
    monkeypatch.setattr(provision_users, "read_existing", register_during_preflight)
    code, result, _ = invoke(path, directory, capsys, "--apply")
    assert code == 2 and "username_collision" in {item["code"] for item in result["conflicts"]}
    users = accounts(store)
    assert len(users) == 1 and users[0]["username"] == "race"


def test_invalid_admin_does_not_create_database(tmp_path, capsys):
    path = write_csv(tmp_path, [("成员甲", "userone")])
    directory = tmp_path / "untouched"
    code, result, _ = invoke(path, directory, capsys, "--apply", "--admin-username", "absent")
    assert code == 2 and result["conflicts"][0]["code"] == "invalid_admin"
    assert not directory.exists()


def test_cli_module_and_direct_script_support_environment_default_without_app_initialization(tmp_path, monkeypatch):
    path = write_csv(tmp_path, [("测试成员", "duan")])
    directory = tmp_path / "no-app-created"
    monkeypatch.setenv("HARBOR_DATA_DIR", str(directory))
    root = Path(__file__).resolve().parents[1]
    for prefix in (["-m", "server.provision_users"], [str(root / "server" / "provision_users.py")]):
        completed = subprocess.run([sys.executable, *prefix, "--input", str(path), "--source", SOURCE], cwd=root, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["mode"] == "dry-run" and result["create"] == 1
        assert "duan" not in completed.stdout and "password_hash" not in completed.stdout
        assert not directory.exists()
