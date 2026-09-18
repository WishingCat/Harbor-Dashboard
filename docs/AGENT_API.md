# Agent 上传 API

Harbor Dashboard 按「项目 → 任务集 → 任务」组织内容，并提供 HTTP API 和零依赖 Python 客户端。Agent 选择任务集，上传本地任务及已有 Rollout 文件后，会获得供团队质检的任务页面链接。

网站左侧底部的「API 接口」提供同类接入说明和示例，也可直接访问 `/?project=PROJECT_ID&view=api`。该页面已包含 Agent 接入所需的全部信息，并可在页面顶部直接生成 API Key；本文是同一套说明的文本版。

## 1. 获取 API Key

登录平台后，在「API 接口」页面顶部的「生成 API Key」区域即可生成、查看和撤销 Key。没有浏览器时按下面的 HTTP 流程获取。

一把 API Key 适用于创建者能访问的**全部项目**，不必为每个项目各生成一把。Agent 可以新建或选择任务集，上传任务、读取任务与评审、追加已有运行产物，以及删除自己上传的任务。上传内容归创建 Key 的用户所有；追加产物和删除任务沿用任务作者或管理员权限。Key 不能用于新建项目、修改平台设置、生成更多 Key 或调用翻译服务。

因为 Key 不绑定项目，**每次调用都必须指定 `project_id`**，省略会返回 `422` 并列出可用项目。项目 ID 可从项目页面 URL 的 `project` 参数、`GET /api/v1/projects` 或客户端的 `projects` 子命令获得。

以下 `HARBOR_API_URL` 为平台网站的基础地址，不含 `/api`。本机开发可使用 `http://localhost:5173`，其他机器上的 Agent 使用可访问的平台域名或服务器地址。

下面先以用户名或邮箱、密码登录，保存会话 Cookie，再创建 API Key。将 `PROJECT_ID` 替换为后续上传的目标项目 ID；有效期为 1–365 天，省略时为 30 天。登录输入不会回显，Key 响应保存到临时文件后读入环境变量。

```bash
export HARBOR_API_URL='https://harbor.example.com'
export HARBOR_PROJECT_ID='PROJECT_ID'
unset HARBOR_API_TOKEN
harbor_auth() {
  local harbor_cookie harbor_token harbor_status
  harbor_cookie="$(mktemp)" || return
  harbor_token="$(mktemp)" || { rm -f -- "$harbor_cookie"; return 1; }
  python3 -c 'import getpass,json; print(json.dumps({"username":getpass.getpass("用户名或邮箱: "),"password":getpass.getpass("密码: ")}))' |
  curl --fail-with-body --silent --show-error \
    "$HARBOR_API_URL/api/auth/login" \
    -c "$harbor_cookie" -H 'Content-Type: application/json' \
    --data-binary @- -o /dev/null &&
  curl --fail-with-body --silent --show-error \
    "$HARBOR_API_URL/api/auth/tokens" \
    -b "$harbor_cookie" -H 'Content-Type: application/json' \
    -d '{"name":"Agent upload","expires_in_days":30}' -o "$harbor_token" &&
  HARBOR_API_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$harbor_token")" &&
  test -n "$HARBOR_API_TOKEN" && export HARBOR_API_TOKEN
  harbor_status=$?
  curl --fail-with-body --silent --show-error \
    "$HARBOR_API_URL/api/auth/logout" -b "$harbor_cookie" -X POST -o /dev/null
  rm -f -- "$harbor_cookie" "$harbor_token"
  return "$harbor_status"
}
harbor_auth
```

创建响应为 `{"token":"...","api_token":{"id":"...","project_id":null,...}}`，明文 `token` 仅在创建时返回，`project_id` 为 `null` 表示适用于全部项目。请将其保存到 Agent 的凭证配置中。使用登录 Cookie 调用 `GET /api/auth/tokens` 可列出本人的 Key 元数据，`DELETE /api/auth/tokens/{token_id}` 可撤销；这两个接口和创建接口均不能使用 Bearer Key 代替登录 Cookie。

在此之前生成、绑定了单个项目的旧 Key 仍然有效，并继续只能访问原项目。

Key 使用 `Authorization: Bearer ...` 请求头传递，不放在 URL 中。远程部署使用现有网站的 HTTPS 地址。

## 2. 用 Python 客户端上传

需要 Python 3.9 或更新版本，无需安装第三方库。可以使用仓库中的 `scripts/harbor_upload.py`，也可以从平台下载：

```bash
curl --fail --show-error "$HARBOR_API_URL/api/agent-client.py" -o harbor_upload.py
python3 harbor_upload.py --help
```

先列出已有任务集，或创建一个新任务集：

```bash
python3 harbor_upload.py task-sets
python3 harbor_upload.py create-task-set '九月任务质检' --json
```

创建响应包含 `task_set.id`、`task_set.name` 和 `task_set.project_id`。将返回的 ID 用于上传，例如：

```bash
export HARBOR_TASK_SET_ID='替换为返回的task_set.id'
python3 harbor_upload.py task /path/to/my-task \
  --task-set "$HARBOR_TASK_SET_ID" \
  --description '待团队质检的任务' \
  --rollout /path/to/trial-one \
  --rollout /path/to/trial-two \
  --json
```

`--task-set ID` 优先于环境变量 `HARBOR_TASK_SET_ID`。配置环境变量后，`task`、`rollout`、`status` 和 `delete` 都会使用该任务集。`--project PROJECT_ID`（或环境变量 `HARBOR_PROJECT_ID`）指定目标项目；账号级 Key 必须提供，缺失时服务端返回 `422` 并列出可用项目。

上传任务目录或 ZIP，简介可省略；以下命令使用环境变量中的任务集：

```bash
python3 harbor_upload.py task /path/to/my-task
python3 harbor_upload.py task /path/to/my-task.zip --description '待团队质检的任务'
```

### 标签

`--tag` 给任务打标签，可重复。预设词表为 `物理`、`化学`、`生物`、`医学`、`人工智能`、`具身智能`、`编程`；这是一份推荐词表而非白名单，任何其他标签同样接受。单个标签不超过 50 个字符，每个任务最多 20 个标签。

```bash
python3 harbor_upload.py task /path/to/my-task --tag 物理 --tag 人工智能
python3 harbor_upload.py tags                       # 预设词表与已用标签及其任务数
python3 harbor_upload.py tasks --tag 物理            # 只列出带该标签的任务
python3 harbor_upload.py tasks --tag 物理 --tag 编程  # 重复即取交集，两个标签都有才列出
```

完全省略 `--tag` 时，沿用任务包 `task.toml` 中 `[metadata] tags` 声明的标签；显式传入一个空列表（`--tag ''`）则清空标签。标签参与幂等指纹：同一个 `Idempotency-Key` 改变标签会返回 `409`。

上传后，客户端显示任务 ID、所属任务集、质检状态和页面链接；加上 `--json` 会输出包含 `task_url` 的 JSON，便于 Agent 解析。把该链接交给评审者即可。任务名称采用 ZIP 文件名或任务目录名，简介可选，分类和难度等从任务清单读取。

之后追加已有产物，以及读取质检进度：

```bash
python3 harbor_upload.py rollout TASK_ID /path/to/job-results --task-set "$HARBOR_TASK_SET_ID"
python3 harbor_upload.py status TASK_ID --task-set "$HARBOR_TASK_SET_ID" --json
```

为兼容原有用法，如果既没有 `--task-set`，也没有 `HARBOR_TASK_SET_ID`，上传新任务会使用项目的默认任务集（不存在时由服务端按需创建）；读取任务和追加产物会识别任务实际所属的任务集。显式指定了任务集时，服务端会校验它与目标任务及请求的项目一致。需要恢复省略任务集的用法时，可先执行 `unset HARBOR_TASK_SET_ID`。

客户端默认根据上传内容及参数（包括任务集 ID）生成 `Idempotency-Key`，相同内容和目标任务集重试会返回原导入结果；明确需要创建一份新导入时使用 `--new-upload`。也可以通过 `--idempotency-key` 设置自己的重试标识。

`create-task-set` 不自动重试。创建请求断连或返回网关错误时，先用 `task-sets` 确认集合是否已经创建，再决定是否重新创建，避免重复集合。

客户端跳过输入目录或 ZIP 顶层的 `.git` 和 `__pycache__`，嵌套在任务环境中的同名目录及其他常规文件会保留；符号链接会被拒绝。上传的脚本、容器配置和结果都作为文件保存。任务中包含可识别的 trial 时，页面展示相应结果；无结果时只展示任务文件。

## 3. 直接使用 HTTP API

交互式文档位于平台 `/docs`，机器可读的接口定义位于 `/openapi.json`。所有 `/api/v1` 接口都使用项目 Token。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/v1/projects` | 查看本 Key 可用的项目 |
| GET | `/api/v1/task-sets` | 查看项目内任务集及任务、运行、评审计数 |
| POST | `/api/v1/task-sets` | 在 Token 项目内新建任务集 |
| GET | `/api/v1/tasks` | 查看项目任务，可用 `task_set_id` 和 `tag` 筛选 |
| GET | `/api/v1/tags` | 读取预设标签词表与项目内已用标签 |
| POST | `/api/v1/tasks` | 上传新任务及包内已有 Rollout |
| GET | `/api/v1/tasks/{task_id}` | 读取任务、文件、已有结果及评审 |
| POST | `/api/v1/tasks/{task_id}/rollouts` | 给已有任务追加结果文件 |
| DELETE | `/api/v1/tasks/{task_id}` | 删除任务，仅作者或管理员 |

**`project_id` 必填**，省略返回 `422` 并列出可用项目（绑定了单个项目的旧 Key 除外，它们可以省略）。任务上传和产物追加通过 multipart 字段传递 `project_id` / `task_set_id`，任务列表、详情和删除通过查询参数传递。任务集省略时沿用上文的兼容规则。

### 新建与列出任务集

新建接口接收 JSON，`name` 去除首尾空白后为 1–80 个字符；`project_id` 可省略：

```bash
curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/task-sets" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"name":"九月任务质检"}'

curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/task-sets" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN"
```

创建响应为 `{"task_set": {...}}`，列表响应为 `{"task_sets": [...]}`。任务集包含 `id`、`project_id`、`name`、`created_at`、`author`、`tasks_count`、`rollouts_count`、`pending_count` 和 `approved_count`。使用返回的 ID，不能用集合名称代替 `task_set_id`。此创建接口没有上传接口的幂等约定，调用方也不应自动重放创建请求。

### 上传任务 ZIP

以下示例中的重试标识应由调用方为一次逻辑上传生成，重试时继续使用相同值。

```bash
curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/tasks" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \
  -H 'Idempotency-Key: review-batch-2026-task-001' \
  -F 'files=@/path/to/my-task.zip' \
  -F "task_set_id=$HARBOR_TASK_SET_ID" \
  -F 'description=可选任务简介' \
  -F 'tags=["物理","人工智能"]'
```

`tags` 是一个 JSON 数组字符串，最多 20 项、每项不超过 50 个字符；省略该字段时保留 `task.toml` 中声明的标签。按标签查询任务时重复 `tag` 查询参数，任务需同时带上全部标签才会返回：

```bash
curl --fail-with-body --show-error --get \
  "$HARBOR_API_URL/api/v1/tasks" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \
  --data-urlencode 'tag=物理' --data-urlencode 'tag=人工智能'

curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/tags" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN"
```

`/api/v1/tags` 返回 `{"preset": [...], "in_use": [{"tag": "物理", "count": 3}, ...]}`，便于 Agent 直接读取词表，不必在客户端硬编码。

上传目录时，推荐使用 Python 客户端。直接发送多个文件时，重复使用 `files` 字段，并在 `paths` 中提供与文件顺序一致的 JSON 相对路径数组，以保留目录结构。

**平台不校验包内格式**：上传内容不必是标准 Harbor 任务，会原样保存并以文件形式展示。包内有唯一 `task.toml` 时从中读取名称、分类、难度与标签；有可识别的 `result.json` 时对应文件作为 Rollout 结果展示。路径安全（越界路径、绝对路径、符号链接、加密 ZIP）与容量限制仍然生效。

上传返回任务记录和链接，并通过 `replayed` 标记是否复用了之前的导入。`task_url` 用于浏览器质检，`api_url` 用于后续 API 查询。关键字段示例：

```json
{
  "task": {
    "id": "TASK_ID",
    "project_id": "project-aa",
    "task_set_id": "TASK_SET_ID",
    "task_set_name": "九月任务质检",
    "title": "my-task",
    "summary": "根据实验设置预测目标量由低到高的顺序，提交 rankings.json，不要求绝对数值。",
    "status": "pending",
    "tags": ["物理", "人工智能"],
    "rollouts_count": 2
  },
  "task_url": "https://harbor.example.com/?project=project-aa&task_set=TASK_SET_ID&task=TASK_ID",
  "api_url": "https://harbor.example.com/api/v1/tasks/TASK_ID?project_id=project-aa&task_set_id=TASK_SET_ID",
  "replayed": false
}
```

完整任务记录还有简介、文件计数和时间等字段。通过 `api_url` 可以读取 `files`、`rollouts` 和 `reviews`。

### 追加结果 ZIP

```bash
curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/tasks/TASK_ID/rollouts" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \
  -H 'Idempotency-Key: review-batch-2026-task-001-results' \
  -F "task_set_id=$HARBOR_TASK_SET_ID" \
  -F 'files=@/path/to/results.zip'
```

可以上传 trial、含多个 trial 的 job，或者保留原始目录结构的产物文件。`name`、`agent`、`model` 为可选项，缺省时从已有结果中识别。结果页展示 Reward、模型、时间、轨迹与原始文件。

### 读取质检状态

```bash
curl --fail-with-body --show-error \
  "$HARBOR_API_URL/api/v1/tasks/TASK_ID?task_set_id=$HARBOR_TASK_SET_ID" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN"
```

响应中的 `task.status` 为 `pending`（待评审）、`approved`（通过）或 `changes_requested`（需修改），`reviews` 包含评审意见，`can_delete` 说明当前 Key 是否有权删除该任务。人工质检状态与上传结果中的 Reward 分开保存。

### 打包下载任务文件

`GET /api/tasks/{task_id}/archive` 返回 ZIP。可选 `prefix` 指定文件夹（省略则打包全部文件），可选 `rollout_id` 打包某个 Rollout 的文件；ZIP 以该文件夹为根目录。

```bash
curl --fail --show-error -o task.zip \
  "$HARBOR_API_URL/api/tasks/TASK_ID/archive?project_id=$HARBOR_PROJECT_ID"

curl --fail --show-error -o tests.zip \
  "$HARBOR_API_URL/api/tasks/TASK_ID/archive?project_id=$HARBOR_PROJECT_ID&prefix=tests"
```

与单文件下载 `GET /api/files/{file_id}/download` 一样，这是公开阅读接口，不需要 Bearer Key。网页端在文件目录里右键文件或文件夹即可下载。

## 删除任务

仅任务作者或管理员可以删除：

```bash
python3 harbor_upload.py delete TASK_ID --project "$HARBOR_PROJECT_ID"

curl --fail-with-body --show-error -X DELETE \
  "$HARBOR_API_URL/api/v1/tasks/TASK_ID?project_id=$HARBOR_PROJECT_ID" \
  -H "Authorization: Bearer $HARBOR_API_TOKEN"
```

响应为 `{"ok": true, "deleted": "TASK_ID"}`。**删除不可撤销**，会一并删除任务文件、全部 Rollout 结果、**其他人提交的评审**以及活动记录。删除请求不会自动重试。删除后使用原 `Idempotency-Key` 重新上传会正常创建新任务，不会返回已删除任务的旧响应。网页端在任务页右上角也有「删除任务」按钮。

## 4. 重试和错误

两个上传接口的 `Idempotency-Key` 最长 128 个字符，作用范围为同一个 Token 和上传路径。相同标识、相同有效载荷及任务集会复用原结果；相同标识提交不同内容或不同任务集返回 `409`。上传失败时事务回滚，可以继续使用原标识重试。客户端最多自动重试两次网络错误或 502/503/504，上传重试保持相同请求体和幂等标识；读取操作同样可以重试。创建任务集不自动重试。

| HTTP 状态 | 含义及处理 |
| --- | --- |
| 400 / 422 | 检查上传包是否完整、路径是否安全、字段是否合法（不再校验任务目录格式） |
| 401 | Token 不存在、已撤销或已过期 |
| 403 | 无权向该任务追加产物 |
| 404 | 项目、任务集或任务不存在，或指定的项目/任务集归属不匹配 |
| 409 | 同一重试标识提交了不同内容，修正标识或恢复原内容 |
| 413 | 超出上传大小或文件数量限制 |
| 5xx / 网络错误 | 上传保留原重试标识；创建任务集先检查列表确认结果 |

单次上传最多 50 MB，展开后最多 100 MB，单文件最多 20 MB。ZIP 展开后最多 2,000 个文件；直接 multipart 上传最多 1,000 个文件，网页目录上传同样受此限制。推荐使用 Python 客户端或 ZIP，客户端会先将目录打包。ZIP 内相对路径会被校验，符号链接、越界路径和重复文件路径会被拒绝。完整规则见上传接口返回的具体错误。

## 5. 部署地址

API 与网页使用同一个服务和端口，无需额外部署 MCP。反向代理转发 `/api`、`/docs` 和 `/openapi.json`，并保留 `Authorization` 请求头和上传请求体。

可在服务器环境配置 `HARBOR_PUBLIC_URL=https://harbor.example.com`，让上传响应中的质检链接使用固定对外域名；未设置时使用当前请求的基础地址。修改配置后重启后端生效。
