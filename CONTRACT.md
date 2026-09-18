# Internal API contract

Stack: React TypeScript + Vite frontend; FastAPI + SQLite backend. Same-origin `/api`, Vite proxy port 8000. Public reads; cookie sessions for writes. First registered user is admin. All dates ISO strings. Errors JSON `{detail: string}`.

Types:
- User `{id,name,email,username:string|null,role:'admin'|'member'}`
- Project `{id,name,tasks_count,rollouts_count,reviews_count}`
- Task `{id,project_id,slug,title,summary,description,category,difficulty:'easy'|'medium'|'hard',tags:string[],status:'pending'|'approved'|'changes_requested',author:string,created_at,updated_at,files_count,rollouts_count,reviews_count,is_demo:boolean}`
- File `{id,path,size,kind:'task'|'rollout',mime_type}`
- Rollout `{id,name,agent,model,status:'passed'|'failed'|'unknown',reward:number|null,duration_seconds:number|null,created_at,files:File[]}`
- Review `{id,author,author_id,verdict:'approved'|'changes_requested'|'comment',body,created_at}`
- Detail `{task:Task,files:File[],rollouts:Rollout[],reviews:Review[]}`

Endpoints (responses specified below):
- GET `/api/health` -> `{status:'ok'}`
- GET `/api/auth/me` -> `{user:User|null}`
- POST `/api/auth/register` `{name,email,password}` -> `{user:User}`
- POST `/api/auth/login` `{username,password}` -> `{user:User}`；username 可填用户名或邮箱，兼容旧 `{email,password}` 请求，标识忽略大小写并去除首尾空格。
- POST `/api/auth/logout` -> `{ok:true}`
- GET `/api/projects` -> `{projects:Project[]}`; fixed IDs `project-aa` (ProjectAA), `paperbenchx` (PaperBenchX).
- GET `/api/tasks?project_id=...` -> `{tasks:Task[]}` (server limits to project, client filters further)
- POST `/api/tasks` multipart: `project_id,title,description,category,difficulty,tags` (tags JSON array), `files` (multiple), `paths` (JSON array preserving relative paths; zip supported) -> `{task:Task}`
- GET `/api/tasks/{id}` -> Detail
- POST `/api/tasks/{id}/rollouts` multipart: `name,agent,model,files,paths` -> `{rollout:Rollout}`
- GET `/api/files/{id}/content` -> `{content:string,truncated:boolean}` text (max 300KB)
- GET `/api/files/{id}/download` -> attachment
- POST `/api/tasks/{id}/reviews` `{verdict,body}` -> `{review:Review}`; verdict changes aggregate task status using latest decision per reviewer, changes_requested wins.
- GET `/api/activity?project_id=...` -> `{activity:[{id,type:'upload'|'review'|'rollout',author,project_id,task_id,task_title,description,created_at}]}`
- GET `/api/settings/translation` -> `{provider,base_url,model,configured:boolean,managed:boolean}` (never return key; managed mode returns an empty base_url)
- PUT `/api/settings/translation` admin-only `{provider,base_url,model,api_key?}` -> same response; blank key preserves existing. Returns 403 when TRANSLATION_MANAGED=true: platform credentials and routing are then supplied exclusively by the server environment. Supports env DEEPSEEK_API_KEY or TRANSLATION_API_KEY.
- POST `/api/translate` `{project_id?,file_id,target_language:'zh'|'en'}` -> SSE `data: {"text":"..."}\n\n`, ending `data: {"done":true}\n\n`, errors `{error:string}`. Auth required. Stream from OpenAI-compatible provider, server owns keys. Translation no code execution.

Project scope: task lists, activity lists and task creation default to `project-aa` for backward compatibility and reject unknown projects. Task detail, rollout/review creation, file content/download accept optional `project_id` query parameters; translation accepts it in JSON. When supplied, it must match the resource's task project or returns 404. The frontend always supplies scope; old task-only links resolve ownership through the unscoped detail endpoint. This groups public content by project; existing login and author/admin permissions remain in force.

Data lives in `data/` (ignored by git). Both projects always exist. With seeding enabled, ProjectAA starts with exactly two clearly marked temporary Harbor tasks and PaperBenchX starts empty; there are no synthetic rollouts or reviews. Seed upgrades preserve user-owned and interacted-with content, replacing only known untouched legacy examples. Stats derive from stored rows. HARBOR_DATA_DIR supports isolated tests.
