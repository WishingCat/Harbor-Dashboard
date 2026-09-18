import json
import os
import sqlite3
import tempfile

import pytest
from fastapi.testclient import TestClient

# Importing the ASGI entry point must not create the developer's real database.
_module_data = tempfile.TemporaryDirectory(prefix="harbor-summary-tests-")
os.environ["HARBOR_DATA_DIR"] = _module_data.name
os.environ["HARBOR_SEED_DEMO"] = "false"
from server.backfill_summaries import main as backfill_main  # noqa: E402
from server.imports import TEXT_LIMIT, task_document, task_slug  # noqa: E402
from server.main import create_app  # noqa: E402
from server.storage import uid  # noqa: E402

BRIEFING = (
    "# 乙酰辅酶A与纯化蛋白的竞争结合排序\n"
    "\n"
    "## 任务摘要\n"
    "\n"
    "根据实验设置预测 mean reported AcCoA-bound scintillation signal（DPM）由低到高的顺序，\n"
    "提交 rankings.json，不要求绝对数值。\n"
    "\n"
    "## 研究背景\n"
    "\n"
    "固定放射性示踪配体浓度、改变未标记配体浓度可用于研究竞争结合。\n"
)
HEADING = "乙酰辅酶A与纯化蛋白的竞争结合排序"
SUMMARY = "根据实验设置预测 mean reported AcCoA-bound scintillation signal（DPM）由低到高的顺序， 提交 rankings.json，不要求绝对数值。"


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


def register(client):
    response = client.post("/api/auth/register", json={"name": "Owner", "email": "owner@example.com", "password": "correct-horse-password"})
    assert response.status_code == 200, response.text
    return response.json()["user"]


def upload(client, entries):
    return client.post("/api/tasks", data={"paths": json.dumps(list(entries))},
                       files=[("files", (path.rsplit("/", 1)[-1], content, "application/octet-stream")) for path, content in entries.items()])


def seed_task(app, data_dir, *, title="my-task", summary="", briefing=BRIEFING, path="任务说明.md"):
    """Insert a task and its briefing blob without going through the upload path."""
    task_id, file_id = uid(), uid()
    body = briefing.encode("utf-8") if briefing is not None else b""
    with app.state.store.connect() as db:
        db.execute("INSERT INTO tasks (id,slug,title,summary,description,category,difficulty,tags,status,author,created_at,updated_at,is_demo,project_id,task_set_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (task_id, task_slug(title, task_id), title, summary, "", "software-engineering", "medium", "[]",
                    "pending", "Owner", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", 0, "project-aa", None))
        if briefing is not None:
            db.execute("INSERT INTO files (id,task_id,rollout_id,path,size,kind,mime_type) VALUES (?,?,?,?,?,?,?)",
                       (file_id, task_id, None, path, len(body), "task", "text/markdown"))
    if briefing is not None:
        (data_dir / "files" / file_id).write_bytes(body)
    return task_id


def legacy_database(directory):
    """A data directory predating the summary column, with one task to migrate."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "files").mkdir(exist_ok=True)
    file_id = uid()
    with sqlite3.connect(directory / "harbor.sqlite3") as db:
        db.executescript("""
            CREATE TABLE tasks (id TEXT PRIMARY KEY, slug TEXT NOT NULL, title TEXT NOT NULL,
                description TEXT NOT NULL, category TEXT NOT NULL, difficulty TEXT NOT NULL,
                tags TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', author_id TEXT,
                author TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0, project_id TEXT NOT NULL DEFAULT 'project-aa',
                task_set_id TEXT);
            CREATE TABLE files (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, rollout_id TEXT,
                path TEXT NOT NULL, size INTEGER NOT NULL, kind TEXT NOT NULL, mime_type TEXT NOT NULL);
        """)
        db.execute("INSERT INTO tasks (id,slug,title,description,category,difficulty,tags,status,author,created_at,updated_at,is_demo,project_id,task_set_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   # Slug matches task_slug(title, id), i.e. the row is machine-named
                   # and therefore eligible for a title replacement.
                   ("legacy01", task_slug("my-task", "legacy01"), "my-task", "", "software-engineering", "medium", "[]",
                    "pending", "Owner", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", 0, "project-aa", None))
        db.execute("INSERT INTO files (id,task_id,rollout_id,path,size,kind,mime_type) VALUES (?,?,?,?,?,?,?)",
                   (file_id, "legacy01", None, "任务说明.md", len(BRIEFING.encode("utf-8")), "task", "text/markdown"))
    (directory / "files" / file_id).write_bytes(BRIEFING.encode("utf-8"))
    return directory


# --- the extractor -----------------------------------------------------------------

def test_reads_the_h1_and_the_summary_section():
    assert task_document({"任务说明.md": BRIEFING.encode()}) == {"title": HEADING, "summary": SUMMARY}


def test_accepts_the_older_combined_heading():
    text = "# 旧标题\n\n## 任务摘要与研究背景\n\n先预测排序，再解释机理。\n\n## 实验设置\n\n略。\n"
    assert task_document({"任务说明.md": text.encode()}) == {"title": "旧标题", "summary": "先预测排序，再解释机理。"}


def test_decodes_a_byte_order_mark():
    assert task_document({"任务说明.md": ("﻿" + BRIEFING).encode("utf-8")})["title"] == HEADING


def test_degrades_without_a_document():
    assert task_document({}) == {"title": "", "summary": ""}
    assert task_document({"instruction.md": BRIEFING.encode()}) == {"title": "", "summary": ""}


def test_degrades_on_undecodable_or_oversized_bytes():
    assert task_document({"任务说明.md": b"\xff\xfe\x00binary"}) == {"title": "", "summary": ""}
    assert task_document({"任务说明.md": b"#" + b"x" * (TEXT_LIMIT + 1)}) == {"title": "", "summary": ""}


def test_reads_a_title_without_a_summary_and_the_reverse():
    assert task_document({"任务说明.md": "# 只有标题\n".encode()}) == {"title": "只有标题", "summary": ""}
    assert task_document({"任务说明.md": "## 任务摘要\n\n只有摘要。\n".encode()}) == {"title": "", "summary": "只有摘要。"}


def test_prefers_the_bundle_root_over_a_nested_copy():
    files = {"my-task/任务说明.md": BRIEFING.encode(), "my-task/rollouts/t1/任务说明.md": "# 别的\n".encode()}
    assert task_document(files, "my-task/")["title"] == HEADING
    # Without a prefix the shallowest candidate still wins.
    assert task_document(files)["title"] == HEADING


def test_joins_a_wrapped_body_and_caps_its_length():
    text = "# 标题\n\n## 任务摘要\n\n第一行\n\n第二行\n第三行\n"
    assert task_document({"任务说明.md": text.encode()})["summary"] == "第一行 第二行 第三行"
    long = "# 标题\n\n## 任务摘要\n\n" + "字" * 900 + "\n"
    assert len(task_document({"任务说明.md": long.encode()})["summary"]) == 600


def test_slug_matches_the_pre_change_expression_for_ascii_titles():
    assert task_slug("my-task", "a0c51c821b494cd0a6471580e84ca4a9") == "my-task-a0c51c"
    # CJK survives \w, which is exactly why the slug must stay on the pack name.
    assert task_slug("乙酰辅酶A", "a0c51c82") == "乙酰辅酶a-a0c51c"


# --- the upload path ---------------------------------------------------------------

def test_upload_derives_title_and_summary_but_never_description(client, app):
    register(client)
    response = upload(client, {"task/task.toml": b'version = "1.0"\n', "task/任务说明.md": BRIEFING.encode()})
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["title"] == HEADING
    assert task["summary"] == SUMMARY
    assert task["description"] == ""
    # The slug still comes from the pack name, never from the Chinese H1: \w matches
    # CJK, so deriving it from the title would make the identifier Chinese too.
    assert task["slug"].startswith("task-")
    assert task["slug"].isascii()


def test_upload_without_a_document_keeps_the_pack_name(client):
    register(client)
    response = upload(client, {"task/task.toml": b'version = "1.0"\n', "task/instruction.md": b"# Stored\n"})
    assert response.status_code == 200, response.text
    task = response.json()["task"]
    assert task["summary"] == ""
    assert task["title"]  # unchanged behaviour: still auto-derived


def test_upload_without_a_document_leaves_summary_empty_in_the_list(client):
    register(client)
    upload(client, {"task/task.toml": b'version = "1.0"\n', "task/instruction.md": b"# Stored\n"})
    rows = client.get("/api/tasks?project_id=project-aa").json()["tasks"]
    assert rows and all(row["summary"] == "" and row["description"] == "" for row in rows)


# --- the backfill ------------------------------------------------------------------

def test_dry_run_reports_without_migrating_or_writing(app, tmp_path, capsys):
    data_dir = legacy_database(tmp_path / "legacy")
    assert backfill_main(["--data-dir", str(data_dir)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry-run" and report["applied"] is False
    assert report["update"] == 1 and report["tasks"][0]["new_title"] == HEADING
    # The strongest statement of "a dry run cannot migrate".
    with sqlite3.connect(data_dir / "harbor.sqlite3") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
    assert "summary" not in columns


def test_apply_writes_title_and_summary_but_leaves_slug_and_updated_at(app, tmp_path, capsys):
    data_dir = legacy_database(tmp_path / "legacy")
    with sqlite3.connect(data_dir / "harbor.sqlite3") as db:
        before = db.execute("SELECT slug,updated_at FROM tasks WHERE id='legacy01'").fetchone()
    assert backfill_main(["--data-dir", str(data_dir), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "apply" and report["written"] == 1
    with sqlite3.connect(data_dir / "harbor.sqlite3") as db:
        after = db.execute("SELECT title,summary,slug,updated_at FROM tasks WHERE id='legacy01'").fetchone()
    assert after[0] == HEADING and after[1] == SUMMARY
    assert (after[2], after[3]) == before


def test_apply_is_idempotent(app, tmp_path, capsys):
    data_dir = legacy_database(tmp_path / "legacy")
    backfill_main(["--data-dir", str(data_dir), "--apply"])
    capsys.readouterr()
    assert backfill_main(["--data-dir", str(data_dir), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["unchanged"] == 1 and report["update"] == 0 and report["written"] == 0


def test_a_hand_chosen_title_is_kept_but_the_summary_still_lands(app, tmp_path, capsys):
    data_dir = tmp_path / "db"
    # A slug that does not match the title proves the title was not machine-derived.
    task_id = seed_task(app, data_dir, title="Renamed by hand", summary="")
    with app.state.store.connect() as db:
        db.execute("UPDATE tasks SET slug='hand-picked-slug' WHERE id=?", (task_id,))
    assert backfill_main(["--data-dir", str(data_dir), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["title_kept"] == 1
    with app.state.store.connect() as db:
        row = db.execute("SELECT title,summary FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row["title"] == "Renamed by hand" and row["summary"] == SUMMARY


def test_a_task_without_a_document_is_left_alone(app, tmp_path, capsys):
    data_dir = tmp_path / "db"
    task_id = seed_task(app, data_dir, title="bare-task", briefing=None)
    assert backfill_main(["--data-dir", str(data_dir), "--apply"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["missing"] == 1 and report["written"] == 0
    with app.state.store.connect() as db:
        row = db.execute("SELECT title,summary FROM tasks WHERE id=?", (task_id,)).fetchone()
    assert row["title"] == "bare-task" and row["summary"] == ""


def test_missing_database_reports_an_error_without_raising(tmp_path, capsys):
    assert backfill_main(["--data-dir", str(tmp_path / "absent")]) == 1
    report = json.loads(capsys.readouterr().out)
    assert "error" in report and report["applied"] is False
