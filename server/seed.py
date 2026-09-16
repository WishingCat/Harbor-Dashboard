"""Two temporary Harbor tasks; no synthetic runs or reviewer decisions."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from textwrap import dedent

from .storage import uid

SEED_MARKER = "seed_project_tasks_v1"
TEMPORARY_AUTHOR = "Harbor 临时任务"

_LEGACY_DEMOS = [
    ("Fix a race condition in the job queue", "修复并发消费者导致的任务重复执行问题，验证锁与重试机制。", "software-engineering", "hard", ["Python", "Concurrency", "Debugging"],
     "A worker occasionally processes a queued job twice when two consumers claim it simultaneously.",
     ["Inspect `src/queue.py` and reproduce the duplicate-claim behavior.", "Make claiming a job atomic without serializing all workers.", "Keep retry and lease-expiration behavior intact.", "Add a regression test with two consumers competing for the same job."], "queue-race-condition"),
    ("Build a reproducible sales forecast", "从带有缺失值的销售数据构建可复现预测流程，避免未来数据泄漏。", "data-science", "medium", ["Python", "Forecasting", "Pandas"],
     "The provided weekly sales dataset includes missing observations and promotions. Produce a forecast for the next four weeks.",
     ["Load the fixture dataset and document missing-value handling.", "Use a chronological training / validation split.", "Compare your model against a seasonal baseline using MAE.", "Save predictions and evaluation notes to `/output`."], "sales-forecast"),
    ("Recover a broken Nginx deployment", "排查配置错误与代理路径问题，让服务恢复并补充健康检查。", "system-administration", "medium", ["Nginx", "Linux", "Networking"],
     "An Nginx reverse proxy returns intermittent 502 errors after a configuration change.",
     ["Inspect the supplied configuration and service logs.", "Fix upstream routing while preserving `/api` request paths.", "Add a health-check endpoint that verifies the upstream service.", "Explain the root cause and provide a repeatable validation command."], "nginx-recovery"),
    ("Implement a sparse matrix solver", "实现稀疏线性系统求解器，验证收敛条件和数值误差。", "scientific-computing", "hard", ["NumPy", "Linear algebra", "Numerical methods"],
     "Implement a conjugate-gradient solver for symmetric positive-definite sparse systems.",
     ["Operate on the provided CSR matrix representation without densifying it.", "Stop when the relative residual meets the requested tolerance.", "Report convergence information and reject invalid dimensions.", "Validate against reference solutions including an ill-conditioned case."], "sparse-matrix-solver"),
    ("Add cursor pagination to the API", "为列表接口增加稳定的游标分页，正确处理重复时间戳与边界条件。", "software-engineering", "easy", ["TypeScript", "API", "PostgreSQL"],
     "The events API currently loads all records into memory. Add stable cursor-based pagination.",
     ["Use `(created_at, id)` as the stable ordering key.", "Return an opaque next cursor and enforce the maximum page size.", "Preserve existing event filters.", "Cover empty pages, repeated timestamps, and malformed cursors."], "cursor-pagination"),
    ("Audit a leaking ML evaluation pipeline", "修复特征处理中的数据泄漏，并以独立测试集重新评估结果。", "data-science", "hard", ["Scikit-learn", "Evaluation", "Data quality"],
     "A classification pipeline reports suspiciously high validation accuracy. Identify and fix data leakage.",
     ["Trace preprocessing and split boundaries.", "Fit every learned transform on the training partition only.", "Remove target-derived and post-outcome features.", "Write a concise comparison of corrected and original metrics."], "evaluation-leakage"),
    ("Harden the database backup workflow", "完善备份脚本的失败处理、完整性校验与恢复演练文档。", "system-administration", "medium", ["Bash", "PostgreSQL", "Reliability"],
     "The nightly backup script reports success even when the database dump is incomplete.",
     ["Propagate failures from every pipeline stage.", "Write backups atomically and verify their integrity.", "Keep credentials out of logs and generated filenames.", "Document a restore drill using an isolated test database."], "database-backup"),
]


def _text(value: str) -> str:
    return dedent(value).lstrip("\n")


_STATS_CLI = _text('''
    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("input", type=Path)
        args = parser.parse_args()
        with args.input.open(encoding="utf-8", newline="") as handle:
            text = handle.read()
        print(json.dumps(count_text(text), ensure_ascii=False))

    if __name__ == "__main__":
        main()
''')
_STATS_HEADER = "import argparse\nimport json\nfrom pathlib import Path\n\n"
_STATS_STARTER = _STATS_HEADER + _text('''
    def count_text(text):
        return {
            "lines": len(text.split("\\n")),
            "words": len(text.split(" ")),
            "characters": len(text.encode("utf-8")),
        }

''') + _STATS_CLI
_STATS_SOLUTION = _STATS_HEADER + _text('''
    def count_text(text):
        return {
            "lines": len(text.splitlines()),
            "words": len(text.split()),
            "characters": len(text),
        }

''') + _STATS_CLI

_METRICS_CLI = _text('''
    def main():
        parser = argparse.ArgumentParser()
        parser.add_argument("input", type=Path)
        parser.add_argument("output", type=Path)
        args = parser.parse_args()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(summarize(args.input), ensure_ascii=False, indent=2) + "\\n",
            encoding="utf-8",
        )

    if __name__ == "__main__":
        main()
''')
_METRICS_HEADER = "import argparse\nimport csv\nimport json\nfrom pathlib import Path\n\n"
_METRICS_STARTER = _METRICS_HEADER + _text('''
    def summarize(path):
        # TODO: group all rows by experiment and calculate the requested summary.
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        return {"experiments": [], "total_rows": len(rows)}

''') + _METRICS_CLI
_METRICS_SOLUTION = _METRICS_HEADER + _text('''
    def summarize(path):
        groups = {}
        total_rows = 0
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                total_rows += 1
                group = groups.setdefault(row["experiment"], {"runs": 0, "values": []})
                group["runs"] += 1
                if row["status"] == "ok":
                    group["values"].append(float(row["accuracy"]))
        summaries = []
        for name, group in sorted(groups.items()):
            values = group["values"]
            summaries.append({
                "experiment": name,
                "runs": group["runs"],
                "successful_runs": len(values),
                "mean_accuracy": round(sum(values) / len(values), 4) if values else None,
            })
        return {"experiments": summaries, "total_rows": total_rows}

''') + _METRICS_CLI


def _manifest(name, description, category, keywords):
    return (
        'schema_version = "1.4"\n\n'
        f'[task]\nname = "project-aa/{name}"\nversion = "0.1.0"\n'
        f'description = {json.dumps(description, ensure_ascii=False)}\n'
        'authors = [{ name = "Harbor Workspace" }]\n'
        f'keywords = {json.dumps(keywords)}\n\n'
        '[metadata]\ntemporary = true\n'
        f'category = "{category}"\ndifficulty = "easy"\n\n'
        '[verifier]\ntimeout_sec = 60.0\n\n'
        '[agent]\ntimeout_sec = 300.0\n\n'
        '[environment]\nbuild_timeout_sec = 300.0\ncpus = 1\nmemory_mb = 512\nstorage_mb = 1024\n'
    )


def _verifier(filename):
    return (
        '#!/usr/bin/env bash\nset -u\nmkdir -p /logs/verifier\n'
        f'if python3 /tests/{filename}; then\n'
        "  printf '1\\n' > /logs/verifier/reward.txt\nelse\n"
        "  printf '0\\n' > /logs/verifier/reward.txt\nfi\n"
    )


def _solution(filename, program):
    return f"#!/usr/bin/env bash\nset -euo pipefail\ncat > /app/{filename} <<'PYTHON'\n{program}PYTHON\n"


def _temporary_tasks():
    stats_description = "修复 UTF-8 文本的行数、词数与字符数统计。"
    metrics_description = "按实验汇总 CSV 中的成功次数和平均准确率。"
    return [
        {
            "slug": "temporary-text-statistics",
            "title": "临时任务 01：修复文本统计脚本",
            "description": stats_description,
            "category": "software-engineering",
            "tags": ["Python", "UTF-8", "临时任务"],
            "files": {
                "instruction.md": _text('''
                    # 修复文本统计脚本

                    > ProjectAA 临时任务。尚无 agent rollout 或评测结果。

                    `/app/text_stats.py` 的 `count_text(text)` 统计结果不正确。
                    修复该函数，保留命令行入口，不添加第三方依赖。

                    ## 统计规则

                    - `lines`：按 Python `str.splitlines()` 的行边界计数；空文件为 0，末尾换行不额外增加一行。
                    - `words`：按连续空白字符分隔；空格、制表符和换行均可分隔词。
                    - `characters`：Unicode 字符数，包含所有空白字符；CRLF 按两个字符计数。

                    `python /app/text_stats.py input.txt` 应读取 UTF-8 文件，并在标准输出写入包含这三个整数的 JSON 对象。
                    输入文件不存在时应以非零状态退出。

                    ## 示例

                    输入 `alpha  beta\\n`（末尾一个换行）：

                    ```json
                    {"lines": 1, "words": 2, "characters": 12}
                    ```

                    验证覆盖空文件、重复空白、中文字符、CRLF 与命令行输入。
                '''),
                "task.toml": _manifest("temporary-text-statistics", stats_description, "software-engineering", ["python", "text"]),
                "environment/Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY text_stats.py /app/text_stats.py\n",
                "environment/text_stats.py": _STATS_STARTER,
                "tests/test.sh": _verifier("test_stats.py"),
                "tests/test_stats.py": _text('''
                    import importlib.util
                    import json
                    from pathlib import Path
                    import subprocess
                    import sys
                    import tempfile
                    import unittest

                    SCRIPT = Path("/app/text_stats.py")

                    class TextStatisticsTests(unittest.TestCase):
                        def setUp(self):
                            spec = importlib.util.spec_from_file_location("text_stats", SCRIPT)
                            self.module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(self.module)

                        def test_statistics(self):
                            cases = [
                                ("", {"lines": 0, "words": 0, "characters": 0}),
                                ("alpha  beta\\n", {"lines": 1, "words": 2, "characters": 12}),
                                ("你好 世界\\n\\n再见", {"lines": 3, "words": 3, "characters": 9}),
                                ("a\\r\\nb\\r\\n", {"lines": 2, "words": 2, "characters": 6}),
                                (" \\t\\n", {"lines": 1, "words": 0, "characters": 3}),
                            ]
                            for text, expected in cases:
                                with self.subTest(text=text):
                                    self.assertEqual(self.module.count_text(text), expected)

                        def test_cli_preserves_crlf(self):
                            with tempfile.TemporaryDirectory() as directory:
                                path = Path(directory) / "input.txt"
                                path.write_bytes(b"a\\r\\nb\\r\\n")
                                result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True)
                                self.assertEqual(result.returncode, 0, result.stderr)
                                self.assertEqual(json.loads(result.stdout), {"lines": 2, "words": 2, "characters": 6})

                        def test_missing_input_fails(self):
                            with tempfile.TemporaryDirectory() as directory:
                                result = subprocess.run([sys.executable, str(SCRIPT), str(Path(directory) / "missing.txt")], capture_output=True)
                                self.assertNotEqual(result.returncode, 0)

                    if __name__ == "__main__":
                        unittest.main()
                '''),
                "solution/solve.sh": _solution("text_stats.py", _STATS_SOLUTION),
            },
        },
        {
            "slug": "temporary-experiment-metrics",
            "title": "临时任务 02：汇总实验指标",
            "description": metrics_description,
            "category": "data-science",
            "tags": ["Python", "CSV", "临时任务"],
            "files": {
                "instruction.md": _text('''
                    # 汇总实验指标

                    > ProjectAA 临时任务。尚无 agent rollout 或评测结果。

                    完成 `/app/aggregate_metrics.py` 中的 `summarize(path)` 函数，仅使用 Python 标准库。
                    输入 CSV 有 `experiment,seed,status,accuracy` 四列，`status` 为 `ok` 或 `failed`；成功行的 accuracy 为 0 到 1 的有效数字，失败行该列可能为空。

                    ## 输出规则

                    - `total_rows`：所有数据行数，不含表头。
                    - `experiments`：按 experiment 名称升序排列的数组。
                    - 每项包含 `experiment`、总行数 `runs`、成功行数 `successful_runs` 和 `mean_accuracy`。
                    - 平均值仅使用成功行，四舍五入到 4 位小数；没有成功行时为 JSON `null`。
                    - 只有表头的输入输出空数组和 `total_rows: 0`。

                    `python /app/aggregate_metrics.py /app/measurements.csv /app/output/summary.json`
                    应自动创建输出目录并写入 UTF-8 JSON，不修改输入文件。

                    示例输入中 baseline 的准确率为 0.7 和 0.9，因此均值为 0.8；failed 行不参与均值。
                    验证还会使用乱序实验、中文名称、全部失败与空输入。
                '''),
                "task.toml": _manifest("temporary-experiment-metrics", metrics_description, "data-science", ["python", "csv", "metrics"]),
                "environment/Dockerfile": "FROM python:3.12-slim\nWORKDIR /app\nCOPY aggregate_metrics.py measurements.csv /app/\n",
                "environment/aggregate_metrics.py": _METRICS_STARTER,
                "environment/measurements.csv": "experiment,seed,status,accuracy\nbaseline,1,ok,0.7\nvariant,1,failed,\nbaseline,2,ok,0.9\nvariant,2,ok,0.86\nbaseline,3,failed,\n",
                "tests/test.sh": _verifier("test_metrics.py"),
                "tests/test_metrics.py": _text('''
                    import importlib.util
                    import json
                    from pathlib import Path
                    import subprocess
                    import sys
                    import tempfile
                    import unittest

                    SCRIPT = Path("/app/aggregate_metrics.py")
                    HEADER = "experiment,seed,status,accuracy\\n"

                    class ExperimentMetricsTests(unittest.TestCase):
                        def summarize(self, rows):
                            spec = importlib.util.spec_from_file_location("aggregate_metrics", SCRIPT)
                            module = importlib.util.module_from_spec(spec)
                            spec.loader.exec_module(module)
                            with tempfile.TemporaryDirectory() as directory:
                                path = Path(directory) / "input.csv"
                                path.write_text(HEADER + rows, encoding="utf-8")
                                return module.summarize(path)

                        def test_groups_are_sorted_and_failures_excluded(self):
                            result = self.summarize("z,1,failed,0.99\\na,1,ok,0.7\\nz,2,ok,0.86\\na,2,ok,0.9\\na,3,failed,\\n")
                            self.assertEqual(result, {"total_rows": 5, "experiments": [
                                {"experiment": "a", "runs": 3, "successful_runs": 2, "mean_accuracy": 0.8},
                                {"experiment": "z", "runs": 2, "successful_runs": 1, "mean_accuracy": 0.86},
                            ]})

                        def test_empty_and_all_failed(self):
                            self.assertEqual(self.summarize(""), {"experiments": [], "total_rows": 0})
                            self.assertEqual(self.summarize("失败实验,1,failed,\\n"), {"total_rows": 1, "experiments": [
                                {"experiment": "失败实验", "runs": 1, "successful_runs": 0, "mean_accuracy": None},
                            ]})

                        def test_rounding(self):
                            result = self.summarize("model,1,ok,0.123456\\nmodel,2,ok,0.8\\n")
                            self.assertEqual(result["experiments"][0]["mean_accuracy"], 0.4617)

                        def test_cli_creates_output_and_preserves_input(self):
                            with tempfile.TemporaryDirectory() as directory:
                                path = Path(directory) / "input.csv"
                                original = (HEADER + "实验,1,ok,0.875\\n").encode("utf-8")
                                path.write_bytes(original)
                                output = Path(directory) / "new" / "summary.json"
                                result = subprocess.run([sys.executable, str(SCRIPT), str(path), str(output)], capture_output=True, text=True)
                                self.assertEqual(result.returncode, 0, result.stderr)
                                self.assertEqual(path.read_bytes(), original)
                                self.assertEqual(json.loads(output.read_text(encoding="utf-8")), {"total_rows": 1, "experiments": [
                                    {"experiment": "实验", "runs": 1, "successful_runs": 1, "mean_accuracy": 0.875},
                                ]})

                    if __name__ == "__main__":
                        unittest.main()
                '''),
                "solution/solve.sh": _solution("aggregate_metrics.py", _METRICS_SOLUTION),
            },
        },
    ]


def _legacy_task_files(demo):
    """Exact input fingerprints from the previous bundled seed, not user uploads."""
    title, _, category, difficulty, tags, context, goals, _ = demo
    instruction = f"# {title}\n\n> Demo task — illustrative content, not a published benchmark.\n\n## Context\n\n{context}\n\n## Your task\n\n" + "\n".join(f"{n}. {goal}" for n, goal in enumerate(goals, 1)) + "\n\n## Acceptance criteria\n\n- The proposed change addresses the root cause and preserves documented behavior.\n- Relevant edge cases have explicit checks.\n- The final summary explains the implementation and how it was verified.\n\n## Environment\n\nTask files use the Harbor layout: `instruction.md`, `task.toml`, `environment/`, and `tests/`. This dashboard stores artifacts for review; it does not execute them.\n\n## Submission\n\nProvide your implementation, validation output, and a short rationale. Reviewers should inspect both the task specification and the rollout artifacts.\n"
    config = f'version = "1.0"\n\n[metadata]\nauthor_name = "Harbor Demo"\nauthor_email = "demo@example.invalid"\ndifficulty = "{difficulty}"\ncategory = "{category}"\ntags = {json.dumps(tags)}\n\n[verifier]\ntimeout_sec = 120.0\n\n[agent]\ntimeout_sec = 600.0\n\n[environment]\nbuild_timeout_sec = 600.0\ncpus = 2\nmemory_mb = 4096\nstorage_mb = 10240\n'
    return {
        "instruction.md": instruction,
        "task.toml": config,
        "environment/Dockerfile": "# Illustrative environment for a demo task.\nFROM python:3.12-slim\nWORKDIR /app\n# Add task-specific fixtures and dependencies before executing in Harbor.\n",
        "tests/test.sh": "#!/usr/bin/env bash\n# Demo placeholder: replace with task-specific assertions before running.\nset -euo pipefail\nprintf '%s\\n' 'This demo has no executable verifier implementation.' >&2\nexit 1\n",
    }


def _file_contents(store, rows):
    contents = {}
    for row in rows:
        # File ids created by Store are UUIDs; refuse unexpected paths or oversized files.
        if len(row["id"]) != 32 or any(c not in "0123456789abcdef" for c in row["id"]) or row["size"] > 100_000:
            return None
        try:
            contents[row["path"]] = (store.blobs / row["id"]).read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
    return contents if len(contents) == len(rows) else None


def _untouched_legacy_task(store, db, task, index, demo):
    title, description, category, difficulty, tags, _, _, slug = demo
    if (task["title"], task["description"], task["category"], task["difficulty"], task["tags"]) != (title, description, category, difficulty, json.dumps(tags)):
        return False
    rows = db.execute("SELECT * FROM files WHERE task_id=? AND rollout_id IS NULL", (task["id"],)).fetchall()
    if _file_contents(store, rows) != _legacy_task_files(demo):
        return False

    # Real reviewers have a user id. The old anonymous synthetic reviewer had this sentinel.
    reviews = db.execute("SELECT * FROM reviews WHERE task_id=?", (task["id"],)).fetchall()
    expected_verdict = "changes_requested" if index in (0, 2, 5) else "approved"
    expected_body = "【演示评审】任务目标清楚，建议再补充异常输入和失败恢复的验收标准。" if expected_verdict == "changes_requested" else "【演示评审】任务说明和验收标准一致，示例产物便于复核。发布前仍需真实运行验证。"
    if len(reviews) > (1 if index in (0, 1, 2, 4, 5) else 0):
        return False
    for review in reviews:
        if (review["author_id"], review["author"], review["verdict"], review["body"]) != ("demo-reviewer", "示例评审员", expected_verdict, expected_body):
            return False
        if db.execute("SELECT 1 FROM users WHERE id=?", (review["author_id"],)).fetchone():
            return False

    rollouts = db.execute("SELECT * FROM rollouts WHERE task_id=?", (task["id"],)).fetchall()
    allowed_names = {f"{slug}__demo-{run + 1}" for run in range((2, 1, 1, 0, 2, 1, 0)[index])}
    if len(rollouts) > len(allowed_names) or len({r["name"] for r in rollouts}) != len(rollouts):
        return False
    for rollout in rollouts:
        if rollout["name"] not in allowed_names:
            return False
        run_files = _file_contents(store, db.execute("SELECT * FROM files WHERE rollout_id=?", (rollout["id"],)).fetchall())
        if not run_files or set(run_files) != {"result.json", "agent/trajectory.json", "verifier/reward.txt", "verifier/test-stdout.txt", "artifacts/summary.md"}:
            return False
        try:
            result = json.loads(run_files["result.json"])
            trajectory = json.loads(run_files["agent/trajectory.json"])
            model = result["agent_info"]["model_info"]
            if not (
                result.get("demo") is True
                and result.get("note") == "Illustrative result; no real model was executed."
                and result.get("task_name") == slug
                and result.get("trial_name") == rollout["name"]
                and result["agent_info"].get("version") == "demo"
                and model.get("provider") == "demo"
                and model.get("name") in {"demo-model-a", "demo-model-b"}
                and trajectory.get("session_id") == rollout["name"]
                and trajectory["agent"].get("version") == "demo"
                and run_files["verifier/test-stdout.txt"].startswith("DEMO OUTPUT — illustrative only\n")
                and "> Demo artifact. No real model execution occurred." in run_files["artifacts/summary.md"]
            ):
                return False
        except (ValueError, KeyError, TypeError, AttributeError):
            return False

    expected_actors = {"upload": "Harbor 示例库", "review": "示例评审员", "rollout": "演示运行"}
    expected_messages = {"upload": "添加了演示任务", "review": "提交了演示评审", "rollout": "添加了示例 rollout 产物"}
    for event in db.execute("SELECT * FROM activity WHERE task_id=?", (task["id"],)):
        if event["author"] != expected_actors.get(event["type"]) or event["description"] != expected_messages.get(event["type"]):
            return False
    return True


def seed_demo(store):
    """Replace untouched legacy examples once, preserving every user-owned interaction."""
    obsolete_blobs = []
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM settings WHERE key=?", (SEED_MARKER,)).fetchone():
            return
        legacy_marker = db.execute("SELECT value FROM settings WHERE key='seed_initialized'").fetchone()
        legacy_demos = _LEGACY_DEMOS if legacy_marker and legacy_marker["value"] == "true" else ()
        for index, demo in enumerate(legacy_demos):
            tasks = db.execute(
                "SELECT * FROM tasks WHERE slug=? AND is_demo=1 AND author_id IS NULL AND author=? AND project_id='project-aa'",
                (demo[-1], "Harbor 示例库"),
            ).fetchall()
            for task in tasks:
                if not _untouched_legacy_task(store, db, task, index, demo):
                    continue
                obsolete_blobs.extend(store.blobs / row["id"] for row in db.execute("SELECT id FROM files WHERE task_id=?", (task["id"],)))
                # Files reference rollouts; delete children first within the same transaction.
                for table in ("files", "reviews", "activity", "rollouts", "tasks"):
                    field = "id" if table == "tasks" else "task_id"
                    db.execute(f"DELETE FROM {table} WHERE {field}=?", (task["id"],))

        timestamp = datetime.now(timezone.utc).isoformat()
        for template in _temporary_tasks():
            task_id = uid()
            db.execute(
                """INSERT INTO tasks
                   (id,slug,title,description,category,difficulty,tags,status,author_id,author,created_at,updated_at,is_demo,project_id)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (task_id, template["slug"], template["title"], template["description"], template["category"], "easy",
                 json.dumps(template["tags"], ensure_ascii=False), "pending", None, TEMPORARY_AUTHOR, timestamp, timestamp, 1, "project-aa"),
            )
            for path, content in template["files"].items():
                store.add_file(db, task_id, path, content.encode("utf-8"))
        db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('seed_initialized','true')")
        db.execute("INSERT INTO settings (key,value) VALUES (?,?)", (SEED_MARKER, "true"))
    # Never remove old blobs before the database transaction has committed.
    for path in obsolete_blobs:
        path.unlink(missing_ok=True)
