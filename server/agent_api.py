"""Project-bound personal tokens and the versioned, non-executing Agent API."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode, urlparse

from fastapi import Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from .archiving import payload_digest
from .storage import now, uid


class CreateTokenBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    project_id: str
    expires_in_days: int = Field(default=30, ge=1, le=365)


class CreateAgentTaskSetBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    project_id: str | None = None


def archive_response_docs(kind):
    """Concrete upload/replay examples visible directly in Swagger UI."""
    task_id, rollout_id, task_set_id = "a" * 32, "b" * 32, "d" * 32
    example = {
        "task_url": f"http://localhost:8000/?project=project-aa&task_set={task_set_id}&task={task_id}",
        "api_url": f"http://localhost:8000/api/v1/tasks/{task_id}?project_id=project-aa&task_set_id={task_set_id}",
        "replayed": False,
    }
    if kind == "task":
        example["task"] = {
            "id": task_id, "slug": "example-task", "title": "example-task", "description": "",
            "category": "General", "difficulty": "Medium", "tags": [], "status": "pending",
            "author": "Example User", "created_at": "2026-09-15T00:00:00+00:00",
            "updated_at": "2026-09-15T00:00:00+00:00", "is_demo": False,
            "project_id": "project-aa", "files_count": 2, "rollouts_count": 0, "reviews_count": 0,
            "task_set_id": task_set_id, "task_set_name": "Example task set",
        }
    else:
        rollout = {
            "id": rollout_id, "name": "existing-trial", "agent": "example-agent", "model": "example-model",
            "status": "completed", "reward": 1.0, "duration_seconds": 30.0,
            "created_at": "2026-09-15T00:00:00+00:00",
            "files": [{"id": "c" * 32, "path": "result.json", "size": 120, "kind": "rollout", "mime_type": "application/json"}],
        }
        example.update(rollout=rollout, rollouts=[rollout], imported_count=1)
        example["task_url"] += f"&rollout={rollout_id}"
    return {
        200: {"description": "Archived successfully, or the saved response with replayed=true for an identical retry.",
              "content": {"application/json": {"examples": {
                  "created": {"summary": "First successful upload", "value": example},
                  "replayed": {"summary": "Identical retry; same IDs, no new activity", "value": {**example, "replayed": True}},
              }}}},
        401: {"description": "Bearer token missing, invalid, expired or revoked. Browser cookies cannot authorize this API."},
        404: {"description": "Task/project does not exist in the token's project."},
        409: {"description": "This token and route already used the Idempotency-Key with different logical files or metadata.",
              "content": {"application/json": {"example": {"detail": "同一 Idempotency-Key 已用于不同的上传内容"}}}},
    }


def token_metadata(row):
    result = {key: row[key] for key in ("id", "name", "project_id", "created_at", "expires_at", "last_used_at", "revoked_at")}
    result["revoked"] = row["revoked_at"] is not None
    return result


def archive_links(request, task, rollout_id=None):
    base = (os.getenv("HARBOR_PUBLIC_URL", "").strip() or str(request.base_url)).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(500, "HARBOR_PUBLIC_URL 必须是有效的网站根地址")
    params = {"project": task["project_id"], "task_set": task["task_set_id"], "task": task["id"]}
    if rollout_id:
        params["rollout"] = rollout_id
    return {"task_url": base + "/?" + urlencode(params),
            "api_url": base + "/api/v1/tasks/" + quote(task["id"], safe="") + "?" + urlencode({"project_id": task["project_id"], "task_set_id": task["task_set_id"]})}


def install_agent_api(app, store, archives, cookie_user, require_project, require_task, list_projects, list_tasks, get_task, require_task_set, list_task_sets):
    bearer = HTTPBearer(auto_error=False, scheme_name="AgentBearer", description="Personal hbr_ token bound to one project. Create it with POST /api/auth/tokens using an authenticated session; it grants only /api/v1 access.")

    def unauthorized():
        return HTTPException(401, "API Token 无效、已过期或已撤销", headers={"WWW-Authenticate": "Bearer"})

    def active_token(db, *, token_hash=None, token_id=None):
        column, value = ("t.token_hash", token_hash) if token_hash is not None else ("t.id", token_id)
        row = db.execute(f"""SELECT t.id token_id,t.project_id,u.id,u.name,u.email,u.role,u.username
            FROM api_tokens t JOIN users u ON u.id=t.user_id
            WHERE {column}=? AND t.revoked_at IS NULL AND t.expires_at>?""", (value, now())).fetchone()
        if not row:
            raise unauthorized()
        return dict(row)

    def agent_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        # This dependency never consults browser cookies, even for an invalid token.
        if credentials is None or not credentials.credentials.startswith("hbr_"):
            raise unauthorized()
        digest = hashlib.sha256(credentials.credentials.encode()).hexdigest()
        with store.connect() as db:
            principal = active_token(db, token_hash=digest)
            db.execute("UPDATE api_tokens SET last_used_at=? WHERE id=?", (now(), principal["token_id"]))
        return principal

    def bound_project(principal, requested):
        if requested is not None and requested != principal["project_id"]:
            raise HTTPException(404, "项目不存在")
        return principal["project_id"]

    def normalized_key(value):
        if value is None:
            return None
        if not value.strip() or len(value) > 128:
            raise HTTPException(422, "Idempotency-Key 必须为 1–128 个字符")
        return value.strip()

    def archive_once(request, principal, project_id, route, key, prepared, operation, task_set_id=None, task_id=None, validate=None):
        with store.connect() as db:
            # A committed reservation always includes the result. A failed import
            # rolls back both the reservation and its files through Store.connect.
            db.execute("BEGIN IMMEDIATE")
            current = active_token(db, token_id=principal["token_id"])
            bound_project(current, project_id)
            require_project(db, project_id)
            if task_id is not None:
                task = require_task(db, task_id, project_id, task_set_id)
                effective_set = task["task_set_id"]
            else:
                effective_set = task_set_id if task_set_id is not None else store.ensure_default_task_set(db, project_id)
            require_task_set(db, effective_set, project_id)
            digest = payload_digest(prepared, project_id, effective_set)
            if validate:
                validate(db, current)
            if key:
                previous = db.execute("SELECT payload_hash,response_json FROM api_idempotency WHERE token_id=? AND route=? AND idempotency_key=?", (current["token_id"], route, key)).fetchone()
                if previous:
                    if previous["response_json"] is None:
                        raise HTTPException(409, "先前请求尚未完成，请稍后重试")
                    saved = json.loads(previous["response_json"])
                    if previous["payload_hash"] != digest:
                        # Keys issued before task sets used only the project in
                        # their hash. Upgrade once after checking the saved task's
                        # actual set, so an old key cannot be replayed into another.
                        saved_id = saved.get("task", {}).get("id") or task_id
                        saved_task = store.task(db, saved_id) if saved_id else None
                        if (previous["payload_hash"] != payload_digest(prepared, project_id)
                                or not saved_task or saved_task["task_set_id"] != effective_set
                                or saved_task["project_id"] != project_id):
                            raise HTTPException(409, "同一 Idempotency-Key 已用于不同的上传内容")
                        if "task" in saved:
                            saved["task"].update(task_set_id=effective_set, task_set_name=saved_task["task_set_name"])
                        saved.update(archive_links(request, saved_task, saved.get("rollout", {}).get("id")))
                        db.execute("UPDATE api_idempotency SET payload_hash=?,response_json=? WHERE token_id=? AND route=? AND idempotency_key=?",
                                   (digest, json.dumps(saved, ensure_ascii=False), current["token_id"], route, key))
                    return {**saved, "replayed": True}
                db.execute("INSERT INTO api_idempotency (token_id,route,idempotency_key,payload_hash,response_json,created_at) VALUES (?,?,?,?,NULL,?)", (current["token_id"], route, key, digest, now()))
            result = operation(db, current, effective_set)
            task = result.get("task") or store.task(db, result.pop("_task_id"))
            first_rollout = result.get("rollout", {}).get("id")
            result.update(archive_links(request, task, first_rollout))
            result["replayed"] = False
            if key:
                db.execute("UPDATE api_idempotency SET response_json=? WHERE token_id=? AND route=? AND idempotency_key=?", (json.dumps(result, ensure_ascii=False), current["token_id"], route, key))
            return result

    @app.get("/api/auth/tokens", tags=["Personal API tokens"], summary="List your personal tokens (browser session required)")
    def get_tokens(user=Depends(cookie_user)):
        with store.connect() as db:
            rows = db.execute("SELECT * FROM api_tokens WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
            return {"tokens": [token_metadata(row) for row in rows]}

    @app.post("/api/auth/tokens", tags=["Personal API tokens"], summary="Create a project-bound token; secret is returned once")
    def create_token(body: CreateTokenBody, user=Depends(cookie_user)):
        if not body.name.strip():
            raise HTTPException(422, "请填写Token名称")
        raw = "hbr_" + secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc)
        token_id = uid()
        with store.connect() as db:
            require_project(db, body.project_id)
            db.execute("""INSERT INTO api_tokens
                (id,user_id,project_id,name,token_hash,created_at,expires_at,last_used_at,revoked_at)
                VALUES (?,?,?,?,?,?,?,NULL,NULL)""", (
                token_id, user["id"], body.project_id, body.name.strip(), hashlib.sha256(raw.encode()).hexdigest(),
                created.isoformat(), (created + timedelta(days=body.expires_in_days)).isoformat(),
            ))
            metadata = token_metadata(db.execute("SELECT * FROM api_tokens WHERE id=?", (token_id,)).fetchone())
        return {"token": raw, "api_token": metadata}

    @app.delete("/api/auth/tokens/{token_id}", tags=["Personal API tokens"], summary="Revoke one of your personal tokens")
    def revoke_token(token_id: str, user=Depends(cookie_user)):
        with store.connect() as db:
            row = db.execute("SELECT id FROM api_tokens WHERE id=? AND user_id=?", (token_id, user["id"])).fetchone()
            if not row:
                raise HTTPException(404, "API Token 不存在")
            db.execute("UPDATE api_tokens SET revoked_at=COALESCE(revoked_at,?) WHERE id=?", (now(), token_id))
        return {"ok": True}

    @app.get("/api/v1/projects", tags=["Agent API v1"], summary="Get the project bound to this Bearer token")
    def agent_projects(principal=Depends(agent_user)):
        return {"projects": [project for project in list_projects()["projects"] if project["id"] == principal["project_id"]]}

    @app.get("/api/v1/task-sets", tags=["Agent API v1"], summary="List task sets in the token's project")
    def agent_task_sets(project_id: str | None = Query(None), principal=Depends(agent_user)):
        return list_task_sets(bound_project(principal, project_id))

    @app.post("/api/v1/task-sets", tags=["Agent API v1"], summary="Create a task set in the token's project")
    def agent_create_task_set(body: CreateAgentTaskSetBody, principal=Depends(agent_user)):
        project_id = bound_project(principal, body.project_id)
        if not body.name.strip():
            raise HTTPException(422, "请填写任务集名称")
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = active_token(db, token_id=principal["token_id"])
            bound_project(current, project_id)
            require_project(db, project_id)
            return {"task_set": store.create_task_set(db, project_id, body.name.strip(), current)}

    @app.get("/api/v1/tasks", tags=["Agent API v1"], summary="List tasks in the token's project")
    def agent_tasks(request: Request, project_id: str | None = Query(None), task_set_id: str | None = Query(None), principal=Depends(agent_user)):
        project_id = bound_project(principal, project_id)
        result = list_tasks(project_id, task_set_id)
        return {"tasks": [{**task, **archive_links(request, task)} for task in result["tasks"]]}

    @app.get("/api/v1/tasks/{task_id}", tags=["Agent API v1"], summary="Inspect task files, existing rollouts and human reviews")
    def agent_task(task_id: str, request: Request, project_id: str | None = Query(None), task_set_id: str | None = Query(None), principal=Depends(agent_user)):
        detail = get_task(task_id, bound_project(principal, project_id), task_set_id)
        return {**detail, **archive_links(request, detail["task"]), "replayed": False}

    @app.post("/api/v1/tasks", tags=["Agent API v1"], summary="Archive a Harbor task and any included rollout artifacts", description="Upload ZIP or directory files with optional paths JSON and description. Nothing is executed. Idempotency-Key (1–128 characters) is scoped to this token and route; identical logical files and metadata replay the original response, changed content returns 409.", responses=archive_response_docs("task"))
    async def agent_create_task(request: Request, files: list[UploadFile] = File(...), paths: str = Form("[]"),
                                description: str = Form(""), project_id: str | None = Form(None), task_set_id: str | None = Form(None),
                                idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal=Depends(agent_user)):
        project_id = bound_project(principal, project_id)
        key = normalized_key(idempotency_key)
        if task_set_id is not None:
            with store.connect() as db:
                require_task_set(db, task_set_id, project_id)
        prepared = await archives.prepare_task(files, paths, description=description)
        return archive_once(request, principal, project_id, "/api/v1/tasks", key, prepared,
                            lambda db, current, effective_set: archives.create_task(db, prepared, current, project_id, effective_set), task_set_id=task_set_id)

    @app.post("/api/v1/tasks/{task_id}/rollouts", tags=["Agent API v1"], summary="Archive existing rollout outputs for a task", description="Only the task author or an administrator may append results, within the token's project. Archives are stored without execution. Idempotency-Key prevents duplicate imports on retry and returns 409 when content changes.", responses=archive_response_docs("rollout"))
    async def agent_create_rollouts(task_id: str, request: Request, files: list[UploadFile] = File(...), paths: str = Form("[]"),
                                    name: str = Form(""), agent: str = Form(""), model: str = Form(""), project_id: str | None = Form(None), task_set_id: str | None = Form(None),
                                    idempotency_key: str | None = Header(None, alias="Idempotency-Key"), principal=Depends(agent_user)):
        project_id = bound_project(principal, project_id)
        key = normalized_key(idempotency_key)

        def check_owner(db, current):
            task = require_task(db, task_id, project_id, task_set_id)
            if current["role"] != "admin" and task["author_id"] != current["id"]:
                raise HTTPException(403, "只有任务作者或管理员可以上传 rollout")

        with store.connect() as db:
            check_owner(db, principal)
        prepared = await archives.prepare_rollouts(files, paths, name=name, agent=agent, model=model)

        def operation(db, current, effective_set):
            return {**archives.create_rollouts(db, task_id, prepared, current), "_task_id": task_id}

        return archive_once(request, principal, project_id, f"/api/v1/tasks/{task_id}/rollouts", key, prepared, operation,
                            task_set_id=task_set_id, task_id=task_id, validate=check_owner)

    @app.get("/api/agent-client.py", tags=["Agent tools"], summary="Download the credential-free Python Agent client")
    def download_agent_client():
        path = Path(__file__).resolve().parent.parent / "scripts" / "harbor_upload.py"
        if not path.is_file():
            raise HTTPException(404, "Agent客户端脚本尚不可用")
        return FileResponse(path, filename="harbor_upload.py", media_type="application/octet-stream")
