"""Seed and migration tests use isolated stores, never the workspace database."""
import json
from pathlib import Path
import subprocess
import sys
import tomllib

import pytest

from server.seed import SEED_MARKER, _LEGACY_DEMOS, _legacy_task_files, seed_demo
from server.storage import Store, uid


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "store")


def project_counts(store):
    with store.connect() as db:
        return {row["id"]: row["count"] for row in db.execute(
            "SELECT p.id,COUNT(t.id) count FROM projects p LEFT JOIN tasks t ON t.project_id=p.id GROUP BY p.id"
        )}


def task_files(store, task_id):
    with store.connect() as db:
        return {row["path"]: (store.blobs / row["id"]).read_text(encoding="utf-8")
                for row in db.execute("SELECT * FROM files WHERE task_id=?", (task_id,))}


def add_task(store, *, legacy_index=None, owner=None, project="project-aa"):
    """Replay the previous bundled input files, or create a user-owned task."""
    task_id = uid()
    if legacy_index is not None:
        demo = _LEGACY_DEMOS[legacy_index]
        title, description, category, difficulty, tags, _, _, slug = demo
        files = _legacy_task_files(demo)
        author, is_demo = "Harbor 示例库", 1
    else:
        title, description, category, difficulty, tags, slug = "用户任务", "应保留", "data-science", "easy", [], "user-created"
        files = {"instruction.md": "# 用户原始内容\n", "task.toml": 'version="1.0"\n'}
        author, is_demo = "User", 0
    with store.connect() as db:
        if legacy_index is not None:
            db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('seed_initialized','true')")
        db.execute(
            """INSERT INTO tasks
               (id,slug,title,description,category,difficulty,tags,status,author_id,author,created_at,updated_at,is_demo,project_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (task_id, slug, title, description, category, difficulty, json.dumps(tags), "pending", owner, author,
             "2026-09-01T00:00:00+00:00", "2026-09-01T00:00:00+00:00", is_demo, project),
        )
        for path, content in files.items():
            store.add_file(db, task_id, path, content.encode())
    return task_id


def add_run(store, task_id, *, synthetic=False):
    rollout_id = uid()
    with store.connect() as db:
        slug = db.execute("SELECT slug FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        name = f"{slug}__demo-1" if synthetic else "actual-user-rollout"
        db.execute(
            "INSERT INTO rollouts (id,task_id,name,agent,model,status,reward,duration_seconds,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (rollout_id, task_id, name, "codex", "demo-model-a" if synthetic else "user-model", "unknown", None, None, "2026-09-02T00:00:00+00:00"),
        )
        result = {"trial_name": name, "task_name": slug, "agent_info": {"version": "demo", "model_info": {"provider": "demo", "name": "demo-model-a"}},
                  "demo": synthetic, "note": "Illustrative result; no real model was executed."}
        contents = {
            "result.json": json.dumps(result),
            "agent/trajectory.json": json.dumps({"session_id": name, "agent": {"version": "demo"}}),
            "verifier/reward.txt": "0\n",
            "verifier/test-stdout.txt": "DEMO OUTPUT — illustrative only\nExample assertion failed\n",
            "artifacts/summary.md": "> Demo artifact. No real model execution occurred.\n",
        }
        for path, content in contents.items():
            store.add_file(db, task_id, path, content.encode(), rollout_id)
    return rollout_id


def test_new_workspace_has_two_temporary_tasks_only_in_project_aa(store):
    seed_demo(store)
    assert project_counts(store) == {"project-aa": 2, "paperbenchx": 0}
    with store.connect() as db:
        tasks = db.execute("SELECT * FROM tasks ORDER BY title").fetchall()
        assert all(t["is_demo"] == 1 and t["status"] == "pending" and t["author_id"] is None for t in tasks)
        assert [t["title"] for t in tasks] == ["临时任务 01：修复文本统计脚本", "临时任务 02：汇总实验指标"]
        assert db.execute("SELECT COUNT(*) FROM rollouts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM activity").fetchone()[0] == 0
    assert [len(task_files(store, task["id"])) for task in tasks] == [7, 8]


def test_repeated_seed_is_idempotent_after_reopening(store):
    seed_demo(store)
    with store.connect() as db:
        before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                  for table in ("tasks", "files", "settings")}
    before_blobs = {path.name: path.read_bytes() for path in store.blobs.iterdir()}
    seed_demo(Store(store.root))
    with store.connect() as db:
        after = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} ORDER BY rowid")]
                 for table in before}
        assert db.execute("SELECT value FROM settings WHERE key=?", (SEED_MARKER,)).fetchone()[0] == "true"
    assert after == before
    assert {path.name: path.read_bytes() for path in store.blobs.iterdir()} == before_blobs


def test_old_bundled_tasks_are_replaced_without_touching_user_tasks(store):
    legacy_ids = [add_task(store, legacy_index=index) for index in range(7)]
    add_run(store, legacy_ids[0], synthetic=True)
    with store.connect() as db:
        db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('seed_initialized','true')")
        db.execute("INSERT INTO reviews (id,task_id,author_id,author,verdict,body,created_at) VALUES (?,?,?,?,?,?,?)",
                   (uid(), legacy_ids[0], "demo-reviewer", "示例评审员", "changes_requested", "【演示评审】任务目标清楚，建议再补充异常输入和失败恢复的验收标准。", "2026-09-01T01:00:00+00:00"))
        old_blob_ids = {row[0] for row in db.execute("SELECT id FROM files")}
    user_id = add_task(store, owner="actual-owner")
    paper_id = add_task(store, owner="paper-owner", project="paperbenchx")
    user_files = task_files(store, user_id)
    paper_files = task_files(store, paper_id)
    seed_demo(store)
    assert project_counts(store) == {"project-aa": 3, "paperbenchx": 1}
    with store.connect() as db:
        remaining = {row[0] for row in db.execute("SELECT id FROM tasks")}
        assert not remaining.intersection(legacy_ids)
        assert {user_id, paper_id}.issubset(remaining)
        assert db.execute("SELECT COUNT(*) FROM rollouts").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    assert task_files(store, user_id) == user_files
    assert task_files(store, paper_id) == paper_files
    assert not any((store.blobs / file_id).exists() for file_id in old_blob_ids)


@pytest.mark.parametrize("interaction", ["review", "rollout", "activity", "edited-file", "owner", "unknown-demo", "no-marker"])
def test_legacy_tasks_with_user_data_are_preserved(store, interaction):
    task_id = add_task(store, legacy_index=0)
    with store.connect() as db:
        if interaction == "review":
            db.execute("INSERT INTO reviews (id,task_id,author_id,author,verdict,body,created_at) VALUES (?,?,?,?,?,?,?)",
                       (uid(), task_id, "actual-user", "真实用户", "comment", "请保留我的评审", "2026-09-02T00:00:00+00:00"))
        elif interaction == "activity":
            db.execute("INSERT INTO activity (id,type,author,task_id,description,created_at) VALUES (?,?,?,?,?,?)",
                       (uid(), "rollout", "真实用户", task_id, "上传了实际产物", "2026-09-02T00:00:00+00:00"))
        elif interaction == "edited-file":
            file_id = db.execute("SELECT id FROM files WHERE task_id=? AND path='instruction.md'", (task_id,)).fetchone()[0]
            (store.blobs / file_id).write_text("# 用户编辑的任务说明\n", encoding="utf-8")
        elif interaction == "owner":
            db.execute("UPDATE tasks SET author_id='actual-user' WHERE id=?", (task_id,))
        elif interaction == "unknown-demo":
            db.execute("UPDATE tasks SET slug='another-demo' WHERE id=?", (task_id,))
        elif interaction == "no-marker":
            db.execute("DELETE FROM settings WHERE key='seed_initialized'")
    if interaction == "rollout":
        add_run(store, task_id)
    files_before = task_files(store, task_id)
    with store.connect() as db:
        records_before = {table: [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE task_id=? ORDER BY rowid", (task_id,))]
                          for table in ("files", "rollouts", "reviews", "activity")}
    seed_demo(store)
    assert project_counts(store) == {"project-aa": 3, "paperbenchx": 0}
    assert task_files(store, task_id) == files_before
    with store.connect() as db:
        for table, records in records_before.items():
            assert [tuple(row) for row in db.execute(f"SELECT * FROM {table} WHERE task_id=? ORDER BY rowid", (task_id,))] == records


def test_failed_seed_rolls_back_legacy_deletion_and_new_blobs(store, monkeypatch):
    legacy_id = add_task(store, legacy_index=0)
    original = store.add_file
    old_files = task_files(store, legacy_id)
    old_blobs = {path.name for path in store.blobs.iterdir()}
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated write failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "add_file", fail_second)
    with pytest.raises(RuntimeError, match="simulated write failure"):
        seed_demo(store)
    assert project_counts(store) == {"project-aa": 1, "paperbenchx": 0}
    assert task_files(store, legacy_id) == old_files
    assert {path.name for path in store.blobs.iterdir()} == old_blobs
    with store.connect() as db:
        assert db.execute("SELECT 1 FROM settings WHERE key=?", (SEED_MARKER,)).fetchone() is None


def test_materials_are_valid_and_verifiers_distinguish_starter_from_solution(store, tmp_path):
    seed_demo(store)
    with store.connect() as db:
        tasks = db.execute("SELECT id FROM tasks ORDER BY title").fetchall()
    for index, task in enumerate(tasks):
        files = task_files(store, task["id"])
        assert files["instruction.md"].startswith("# ")
        assert "尚无 agent rollout 或评测结果" in files["instruction.md"]
        config = tomllib.loads(files["task.toml"])
        assert config["schema_version"] == "1.4" and config["metadata"]["temporary"] is True
        assert config["task"]["name"].startswith("project-aa/")
        assert isinstance(config["task"]["authors"][0], dict)
        program_name = "text_stats.py" if index == 0 else "aggregate_metrics.py"
        test_name = "test_stats.py" if index == 0 else "test_metrics.py"
        directory = tmp_path / f"material-{index}"
        directory.mkdir()
        program = directory / program_name
        verifier = directory / test_name
        program.write_text(files[f"environment/{program_name}"], encoding="utf-8")
        adapted_test = files[f"tests/{test_name}"].replace(f'Path("/app/{program_name}")', f"Path({str(program)!r})")
        verifier.write_text(adapted_test, encoding="utf-8")
        for path, source in files.items():
            if path.endswith(".py"):
                compile(source, path, "exec")
            elif path.endswith(".sh"):
                result = subprocess.run(["bash", "-n"], input=source, capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
        broken = subprocess.run([sys.executable, str(verifier)], capture_output=True, text=True)
        assert broken.returncode != 0, "The unfinished starter must not satisfy its verifier"
        reference = files["solution/solve.sh"].split("<<'PYTHON'\n", 1)[1].rsplit("PYTHON\n", 1)[0]
        program.write_text(reference, encoding="utf-8")
        corrected = subprocess.run([sys.executable, str(verifier)], capture_output=True, text=True)
        assert corrected.returncode == 0, corrected.stderr
    stats_files = task_files(store, tasks[0]["id"])
    assert "`alpha  beta\\n`" in stats_files["instruction.md"]
