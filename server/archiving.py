"""Shared non-executing archive imports for browser and Agent HTTP clients."""
from __future__ import annotations

import hashlib
import json

from fastapi import HTTPException

from .imports import is_task_material, parse_rollout, read_uploads, task_bundle_prefix, task_document, task_metadata, task_slug, trial_groups, upload_title
from .storage import now, uid

# Text derived purely from the uploaded files. It carries no uploader intent, so it
# stays out of the logical-upload digest: Idempotency-Keys issued before these fields
# existed keep matching, and a retry in flight across a deploy is not rejected.
DERIVED_FIELDS = frozenset({"summary", "slug_base"})


def payload_digest(prepared, project_id, task_set_id=None):
    # Compare the logical upload, not transport boundaries or ZIP timestamps.
    fields = {key: value for key, value in prepared["fields"].items() if key not in DERIVED_FIELDS}
    canonical = {"project_id": project_id, "fields": fields,
                 "files": [[path, hashlib.sha256(data).hexdigest()] for path, data in sorted(prepared["files"].items())]}
    if task_set_id is not None:
        canonical["task_set_id"] = task_set_id
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


class ArchiveManager:
    def __init__(self, store):
        self.store = store

    async def prepare_task(self, files, paths, *, title="", description="", category="", difficulty="", tags=""):
        if difficulty and difficulty not in {"easy", "medium", "hard"}:
            raise HTTPException(422, "任务难度无效")
        if len(title) > 200 or len(description) > 10000 or len(category) > 100:
            raise HTTPException(422, "标题、描述或分类过长")
        try:
            tag_list = json.loads(tags) if tags.strip() else None
        except ValueError:
            raise HTTPException(422, "tags 必须是 JSON 数组")
        if tag_list is not None and (not isinstance(tag_list, list) or len(tag_list) > 20 or any(not isinstance(t, str) or len(t) > 50 for t in tag_list)):
            raise HTTPException(422, "最多支持 20 个标签，每个标签不超过 50 字符")
        imported = await read_uploads(files, paths)
        prefix = task_bundle_prefix(imported)
        metadata = task_metadata(imported)
        document = task_document(imported, prefix)
        submitted_title = title.strip()
        source_title = submitted_title or upload_title(files, paths) or metadata.get("title") or "未命名任务"
        # An explicitly submitted title still wins. Otherwise the task's own Chinese
        # H1 replaces the auto-derived pack name, while the slug keeps that pack name.
        display_title = submitted_title or document["title"] or source_title
        return {"files": imported, "prefix": prefix, "fields": {
            "title": display_title[:200], "slug_base": source_title, "summary": document["summary"],
            "description": description.strip(),
            "category": category.strip() or metadata.get("category", "software-engineering"),
            "difficulty": difficulty or metadata.get("difficulty", "medium"),
            "tags": [t.strip() for t in (tag_list if tag_list is not None else metadata.get("tags", [])) if t.strip()],
        }}

    async def prepare_rollouts(self, files, paths, *, name="", agent="", model=""):
        if any(len(value) > 200 for value in (name, agent, model)):
            raise HTTPException(422, "Rollout 名称、agent 或 model 不能超过 200 字符")
        return {"files": await read_uploads(files, paths), "fields": {"name": name, "agent": agent, "model": model}}

    def add_rollout(self, db, task_id, files, supplied=None, result=None):
        parsed = parse_rollout(files, supplied, result)
        rollout_id = uid()
        db.execute("INSERT INTO rollouts (id,task_id,name,agent,model,status,reward,duration_seconds,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (
            rollout_id, task_id, parsed["name"], parsed["agent"], parsed["model"],
            parsed["status"], parsed["reward"], parsed["duration_seconds"], now(),
        ))
        for path, data in files.items():
            self.store.add_file(db, task_id, path, data, rollout_id)
        return rollout_id

    def create_task(self, db, prepared, user, project_id, task_set_id=None):
        task_set_id = task_set_id or self.store.ensure_default_task_set(db, project_id)
        fields, imported = prepared["fields"], prepared["files"]
        task_id = uid()
        slug = task_slug(fields.get("slug_base") or fields["title"], task_id)
        timestamp = now()
        db.execute("""INSERT INTO tasks
            (id,slug,title,summary,description,category,difficulty,tags,status,author_id,author,created_at,updated_at,is_demo,project_id,task_set_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
            task_id, slug, fields["title"], fields.get("summary", ""), fields["description"], fields["category"], fields["difficulty"],
            json.dumps(fields["tags"], ensure_ascii=False), "pending", user["id"], user["name"], timestamp, timestamp, 0, project_id, task_set_id,
        ))
        groups = trial_groups(imported, task_prefix=prepared["prefix"])
        consumed = set()
        for prefix, result in groups:
            members = {p[len(prefix):]: data for p, data in imported.items()
                       if p.startswith(prefix) and not is_task_material(p, prepared["prefix"])}
            if members:
                self.add_rollout(db, task_id, members, result=result)
                consumed.update(prefix + p for p in members)
        for path, data in imported.items():
            if path not in consumed:
                self.store.add_file(db, task_id, path, data)
        self.store.add_activity(db, "upload", user["name"], task_id, "上传了新任务")
        return {"task": self.store.task(db, task_id)}

    def create_rollouts(self, db, task_id, prepared, user):
        imported, fields = prepared["files"], prepared["fields"]
        groups = trial_groups(imported)
        rollout_ids = []
        if groups:
            consumed = {p for p in imported if any(p.startswith(prefix) for prefix, _ in groups)}
            shared_files = {p: value for p, value in imported.items() if p not in consumed}
            for index, (prefix, result) in enumerate(groups):
                members = {p[len(prefix):]: data for p, data in imported.items() if p.startswith(prefix)}
                if index == 0 and shared_files:
                    shared_root, suffix = "_job", 2
                    while any(p == shared_root or p.startswith(shared_root + "/") for p in members):
                        shared_root = f"_job_{suffix}"
                        suffix += 1
                    members.update({f"{shared_root}/{p}": value for p, value in shared_files.items()})
                supplied = {**fields, "name": fields["name"] if len(groups) == 1 else ""}
                rollout_ids.append(self.add_rollout(db, task_id, members, supplied, result))
        else:
            rollout_ids.append(self.add_rollout(db, task_id, imported, fields))
        db.execute("UPDATE tasks SET updated_at=? WHERE id=?", (now(), task_id))
        self.store.add_activity(db, "rollout", user["name"], task_id, f"上传了 {len(rollout_ids)} 次 rollout")
        rollouts = [self.store.rollout(db, rollout_id) for rollout_id in rollout_ids]
        return {"rollout": rollouts[0], "rollouts": rollouts, "imported_count": len(rollouts)}
