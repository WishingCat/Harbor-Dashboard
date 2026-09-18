"""Backfill task titles and summaries from each task's own 任务说明.md.

Usage: python -m server.backfill_summaries [--data-dir PATH] [--apply]
The default is a read-only dry run. Pass --apply to write the derived text.

A title is replaced only when the stored one is provably the name its slug was
derived from, so a hand-chosen title is never overwritten. Slugs and updated_at
are never touched: updated_at drives the library ordering.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path, PurePosixPath

# Support both `python -m server.backfill_summaries` and a direct script invocation.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from server.imports import TASK_DOCUMENT, task_document, task_slug
    from server.storage import Store
else:
    from .imports import TASK_DOCUMENT, task_document, task_slug
    from .storage import Store


def machine_named(row) -> bool:
    """True when the stored title is exactly what the slug was derived from."""
    return task_slug(row["title"], row["id"]) == row["slug"]


def read_rows(db, columns, *, project=None, task=None, limit=None):
    """Select the tasks to consider, tolerating a database without the column yet."""
    selected = ["id", "slug", "title", "project_id"]
    selected.append("summary" if "summary" in columns else "'' AS summary")
    where, params = [], []
    if project:
        where.append("project_id=?")
        params.append(project)
    if task:
        where.append("id=?")
        params.append(task)
    query = "SELECT " + ",".join(selected) + " FROM tasks"
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY created_at"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    rows = db.execute(query, params)
    names = [column[0] for column in rows.description]
    return [dict(zip(names, row)) for row in rows]


def collect_document(db, data_dir: Path, task_id: str):
    """Read only the briefing blobs belonging to this task; nothing is written."""
    files = {}
    for file_id, path in db.execute(
        "SELECT id,path FROM files WHERE task_id=? AND rollout_id IS NULL", (task_id,)
    ):
        if PurePosixPath(path).name != TASK_DOCUMENT:
            continue
        try:
            files[path] = (data_dir / "files" / file_id).read_bytes()
        except OSError:
            continue
    return files, task_document(files)


def plan_task(row, files, document, *, force_titles):
    title = row["title"]
    summary = row["summary"]
    new_title = document["title"] or title
    title_kept = bool(document["title"]) and not force_titles and not machine_named(row)
    if title_kept:
        new_title = title
    new_summary = document["summary"] or summary
    if not files:
        status = "missing"
    elif not document["title"] and not document["summary"]:
        status = "empty"
    elif new_title != title or new_summary != summary:
        status = "update"
    else:
        status = "unchanged"
    return {"id": row["id"], "slug": row["slug"], "title": title, "new_title": new_title,
            "summary_chars": len(new_summary), "status": status, "title_kept": title_kept,
            "_old_summary": summary, "_new_summary": new_summary}


def preflight(data_dir: Path, *, project=None, task=None, limit=None, force_titles=False):
    """Read-only scan. Never constructs Store, so a dry run cannot migrate."""
    database = data_dir / "harbor.sqlite3"
    if not database.exists():
        raise FileNotFoundError(f"No database at {database}")
    # A read-only connection sees committed WAL records without initializing Store.
    db = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        columns = {name for _, name, *_ in db.execute("PRAGMA table_info(tasks)")}
        planned = []
        for row in read_rows(db, columns, project=project, task=task, limit=limit):
            files, document = collect_document(db, data_dir, row["id"])
            planned.append(plan_task(row, files, document, force_titles=force_titles))
        return planned
    finally:
        db.close()


def report(data_dir, mode, planned, *, applied, written=None):
    result = {"mode": mode, "applied": applied, "data_dir": str(data_dir), "total": len(planned),
              "update": sum(item["status"] == "update" for item in planned),
              "unchanged": sum(item["status"] == "unchanged" for item in planned),
              "missing": sum(item["status"] == "missing" for item in planned),
              "empty": sum(item["status"] == "empty" for item in planned),
              "title_kept": sum(item["title_kept"] for item in planned),
              "tasks": [{key: value for key, value in item.items() if not key.startswith("_")} for item in planned]}
    if written is not None:
        result["written"] = written
    return result


def backfill(data_dir, *, project=None, task=None, limit=None, force_titles=False, apply=False):
    data_dir = Path(data_dir).resolve()
    planned = preflight(data_dir, project=project, task=task, limit=limit, force_titles=force_titles)
    if not apply:
        return report(data_dir, "dry-run", planned, applied=False)
    store = Store(data_dir)
    written = 0
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        # Recheck under the write lock: the derived text is a pure function of blobs
        # that this command never writes, but a concurrent edit between the preflight
        # and this write must not be clobbered.
        for item in planned:
            if item["status"] != "update":
                continue
            current = db.execute("SELECT title,summary FROM tasks WHERE id=?", (item["id"],)).fetchone()
            if current is None or current["title"] != item["title"] or current["summary"] != item["_old_summary"]:
                continue
            db.execute("UPDATE tasks SET title=?,summary=? WHERE id=?",
                       (item["new_title"], item["_new_summary"], item["id"]))
            written += 1
    return report(data_dir, "apply", planned, applied=True, written=written)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Backfill task titles and summaries from 任务说明.md; defaults to a dry run.")
    parser.add_argument("--data-dir", type=Path, default=Path(os.getenv("HARBOR_DATA_DIR", Path(__file__).resolve().parent.parent / "data")))
    parser.add_argument("--apply", action="store_true", help="Write the derived titles and summaries")
    parser.add_argument("--project", help="Restrict to one project id")
    parser.add_argument("--task", help="Restrict to a single task id")
    parser.add_argument("--limit", type=int, help="Stop after this many tasks")
    parser.add_argument("--force-titles", action="store_true", help="Replace titles even when they were not machine-derived")
    args = parser.parse_args(argv)
    mode = "apply" if args.apply else "dry-run"
    try:
        result = backfill(args.data_dir, project=args.project, task=args.task, limit=args.limit,
                          force_titles=args.force_titles, apply=args.apply)
        code = 0
    except (sqlite3.Error, OSError) as exc:
        result = {"mode": mode, "applied": False, "data_dir": str(args.data_dir),
                  "error": f"The backfill could not complete; no task was modified ({exc.__class__.__name__})."}
        code = 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
