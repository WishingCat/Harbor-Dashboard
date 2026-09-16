from __future__ import annotations

import json
import mimetypes
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

# Curated subject tags offered in the upload form and documented for Agent
# clients. Uploads may also carry any other tag; this list is not a whitelist.
PRESET_TAGS = ["物理", "化学", "生物", "医学", "人工智能", "具身智能", "编程"]

DEFAULT_PROJECT = "project-aa"
PROJECTS = ((DEFAULT_PROJECT, "ProjectAA"), ("paperbenchx", "PaperBenchX"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def uid() -> str:
    return uuid.uuid4().hex


class FileTransaction(sqlite3.Connection):
    """Track only blobs created by this connection so rollback leaves no orphans."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.created_blobs: list[Path] = []


class Store:
    def __init__(self, directory: str | Path):
        self.root = Path(directory).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / "files"
        self.blobs.mkdir(exist_ok=True, mode=0o700)
        self.database = self.root / "harbor.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, role TEXT NOT NULL, created_at TEXT NOT NULL,
                    username TEXT COLLATE NOCASE, external_id TEXT
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_sets (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    name TEXT NOT NULL, author_id TEXT, author TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_tokens (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    project_id TEXT NOT NULL REFERENCES projects(id), name TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL, last_used_at TEXT, revoked_at TEXT
                );
                CREATE TABLE IF NOT EXISTS api_idempotency (
                    token_id TEXT NOT NULL REFERENCES api_tokens(id), route TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    response_json TEXT, created_at TEXT NOT NULL,
                    PRIMARY KEY(token_id,route,idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, slug TEXT NOT NULL, title TEXT NOT NULL,
                    description TEXT NOT NULL, category TEXT NOT NULL, difficulty TEXT NOT NULL,
                    tags TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                    author_id TEXT, author TEXT NOT NULL, created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0,
                    project_id TEXT NOT NULL DEFAULT 'project-aa' REFERENCES projects(id),
                    task_set_id TEXT REFERENCES task_sets(id)
                );
                CREATE TABLE IF NOT EXISTS rollouts (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    name TEXT NOT NULL, agent TEXT NOT NULL, model TEXT NOT NULL,
                    status TEXT NOT NULL, reward REAL, duration_seconds REAL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    rollout_id TEXT REFERENCES rollouts(id), path TEXT NOT NULL,
                    size INTEGER NOT NULL, kind TEXT NOT NULL, mime_type TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reviews (
                    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
                    author_id TEXT NOT NULL, author TEXT NOT NULL, verdict TEXT NOT NULL,
                    body TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS activity (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, author TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(id), description TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS idx_files_task ON files(task_id);
                CREATE INDEX IF NOT EXISTS idx_reviews_task ON reviews(task_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_rollouts_task ON rollouts(task_id);
                CREATE INDEX IF NOT EXISTS idx_api_tokens_user ON api_tokens(user_id,created_at);
                CREATE INDEX IF NOT EXISTS idx_task_sets_project ON task_sets(project_id,created_at);
            """)
            db.executemany("INSERT INTO projects (id,name) VALUES (?,?) ON CONFLICT(id) DO NOTHING", PROJECTS)
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            user_columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
            if "username" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN username TEXT COLLATE NOCASE")
            if "external_id" not in user_columns:
                db.execute("ALTER TABLE users ADD COLUMN external_id TEXT")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username_unique ON users(username COLLATE NOCASE)")
            db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_external_id_unique ON users(external_id)")
            db.commit()
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "project_id" not in columns:
                # SQLite cannot add a non-NULL REFERENCES default while FK checks
                # are on. ALTER preserves task rowids and all existing child rows.
                db.execute("PRAGMA foreign_keys=OFF")
                try:
                    db.execute("BEGIN IMMEDIATE")
                    columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
                    if "project_id" not in columns:
                        db.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT NOT NULL DEFAULT 'project-aa' REFERENCES projects(id)")
                    if db.execute("PRAGMA foreign_key_check").fetchone():
                        raise sqlite3.IntegrityError("Project migration found invalid foreign-key references")
                    db.commit()
                except BaseException:
                    db.rollback()
                    raise
                finally:
                    db.execute("PRAGMA foreign_keys=ON")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id,created_at)")
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in db.execute("PRAGMA table_info(tasks)")}
            if "task_set_id" not in columns:
                # Adding the nullable FK preserves rowids and every child reference.
                # Backfill before exposing the upgraded Store; legacy SQL inserts
                # are subsequently assigned a default by the compatibility trigger.
                db.execute("ALTER TABLE tasks ADD COLUMN task_set_id TEXT REFERENCES task_sets(id)")
            for row in db.execute("SELECT DISTINCT project_id FROM tasks WHERE task_set_id IS NULL").fetchall():
                task_set_id = self.ensure_default_task_set(db, row["project_id"])
                db.execute("UPDATE tasks SET task_set_id=? WHERE project_id=? AND task_set_id IS NULL", (task_set_id, row["project_id"]))
            if db.execute("SELECT 1 FROM tasks t LEFT JOIN task_sets s ON s.id=t.task_set_id WHERE s.id IS NULL OR s.project_id!=t.project_id LIMIT 1").fetchone():
                raise sqlite3.IntegrityError("Task-set migration found inconsistent project references")
            db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_task_set ON tasks(task_set_id,created_at)")
            db.execute("""CREATE TRIGGER IF NOT EXISTS tasks_check_set_insert BEFORE INSERT ON tasks
                WHEN NEW.task_set_id IS NOT NULL AND NOT EXISTS
                    (SELECT 1 FROM task_sets WHERE id=NEW.task_set_id AND project_id=NEW.project_id)
                BEGIN SELECT RAISE(ABORT, 'Task set must belong to the task project'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS tasks_check_set_update BEFORE UPDATE OF project_id,task_set_id ON tasks
                WHEN NEW.task_set_id IS NULL OR NOT EXISTS
                    (SELECT 1 FROM task_sets WHERE id=NEW.task_set_id AND project_id=NEW.project_id)
                BEGIN SELECT RAISE(ABORT, 'Task set must belong to the task project'); END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS tasks_default_set AFTER INSERT ON tasks
                WHEN NEW.task_set_id IS NULL BEGIN
                    INSERT OR IGNORE INTO task_sets (id,project_id,name,author_id,author,created_at)
                    VALUES ('default:' || NEW.project_id,NEW.project_id,'默认任务集',NULL,'系统',NEW.created_at);
                    UPDATE tasks SET task_set_id='default:' || NEW.project_id WHERE id=NEW.id;
                END""")
            db.execute("""CREATE TRIGGER IF NOT EXISTS task_sets_check_project_update BEFORE UPDATE OF project_id ON task_sets
                WHEN EXISTS (SELECT 1 FROM tasks WHERE task_set_id=OLD.id AND project_id!=NEW.project_id)
                BEGIN SELECT RAISE(ABORT, 'Task set project must match its tasks'); END""")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise sqlite3.IntegrityError("Task-set migration found invalid foreign-key references")
        os.chmod(self.database, 0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=30, factory=FileTransaction)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            for blob in db.created_blobs:
                blob.unlink(missing_ok=True)
            raise
        finally:
            db.close()

    def add_file(self, db, task_id, path, content: bytes, rollout_id=None):
        file_id = uid()
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.endswith((".md", ".toml", ".sh", ".py", ".log", ".txt", ".yaml", ".yml")):
            mime = "text/plain"
        blob = self.blobs / file_id
        with blob.open("xb") as handle:
            db.created_blobs.append(blob)
            handle.write(content)
        os.chmod(blob, 0o600)
        db.execute("INSERT INTO files VALUES (?,?,?,?,?,?,?)", (
            file_id, task_id, rollout_id, path, len(content), "rollout" if rollout_id else "task", mime,
        ))
        return file_id

    def ensure_default_task_set(self, db, project_id):
        task_set_id = "default:" + project_id
        db.execute("""INSERT INTO task_sets (id,project_id,name,author_id,author,created_at)
            VALUES (?,?,?,NULL,?,?) ON CONFLICT(id) DO NOTHING""", (task_set_id, project_id, "默认任务集", "系统", now()))
        return task_set_id

    def project(self, db, project_id):
        row = db.execute("""SELECT p.id,p.name,
            (SELECT COUNT(*) FROM task_sets s WHERE s.project_id=p.id) task_sets_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.project_id=p.id) tasks_count,
            (SELECT COUNT(*) FROM rollouts r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=p.id) rollouts_count,
            (SELECT COUNT(*) FROM reviews r JOIN tasks t ON t.id=r.task_id WHERE t.project_id=p.id) reviews_count
            FROM projects p WHERE p.id=?""", (project_id,)).fetchone()
        return dict(row) if row else None

    def task_set(self, db, task_set_id):
        row = db.execute("""SELECT s.id,s.project_id,s.name,s.created_at,s.author,
            (SELECT COUNT(*) FROM tasks t WHERE t.task_set_id=s.id) tasks_count,
            (SELECT COUNT(*) FROM rollouts r JOIN tasks t ON t.id=r.task_id WHERE t.task_set_id=s.id) rollouts_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.task_set_id=s.id AND t.status='pending') pending_count,
            (SELECT COUNT(*) FROM tasks t WHERE t.task_set_id=s.id AND t.status='approved') approved_count
            FROM task_sets s WHERE s.id=?""", (task_set_id,)).fetchone()
        return dict(row) if row else None

    def create_task_set(self, db, project_id, name, user):
        task_set_id = uid()
        db.execute("INSERT INTO task_sets (id,project_id,name,author_id,author,created_at) VALUES (?,?,?,?,?,?)",
                   (task_set_id, project_id, name, user["id"], user["name"], now()))
        return self.task_set(db, task_set_id)

    def task(self, db, task_id):
        row = db.execute("""
            SELECT t.*,
                (SELECT name FROM task_sets s WHERE s.id=t.task_set_id) task_set_name,
                (SELECT COUNT(*) FROM files f WHERE f.task_id=t.id AND f.kind='task') files_count,
                (SELECT COUNT(*) FROM rollouts r WHERE r.task_id=t.id) rollouts_count,
                (SELECT COUNT(*) FROM reviews v WHERE v.task_id=t.id) reviews_count
            FROM tasks t WHERE t.id=?
        """, (task_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result.pop("author_id")
        result["tags"] = json.loads(result["tags"])
        result["is_demo"] = bool(result["is_demo"])
        return result

    def file_list(self, db, task_id=None, rollout_id=None):
        if rollout_id:
            rows = db.execute("SELECT id,path,size,kind,mime_type FROM files WHERE rollout_id=? ORDER BY path", (rollout_id,))
        else:
            rows = db.execute("SELECT id,path,size,kind,mime_type FROM files WHERE task_id=? AND rollout_id IS NULL ORDER BY path", (task_id,))
        return [dict(r) for r in rows]

    def rollout(self, db, rollout_id):
        row = db.execute("SELECT * FROM rollouts WHERE id=?", (rollout_id,)).fetchone()
        result = dict(row)
        result.pop("task_id")
        result["files"] = self.file_list(db, rollout_id=rollout_id)
        return result

    def add_activity(self, db, kind, author, task_id, description):
        db.execute("INSERT INTO activity VALUES (?,?,?,?,?,?)", (uid(), kind, author, task_id, description, now()))

    def recalculate_status(self, db, task_id):
        decisions = {}
        for row in db.execute("SELECT author_id,verdict FROM reviews WHERE task_id=? AND verdict!='comment' ORDER BY created_at,rowid", (task_id,)):
            decisions[row["author_id"]] = row["verdict"]
        values = decisions.values()
        status = "changes_requested" if "changes_requested" in values else "approved" if "approved" in values else "pending"
        db.execute("UPDATE tasks SET status=?, updated_at=? WHERE id=?", (status, now(), task_id))
