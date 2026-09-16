# Harbor Rollout Dashboard

用于上传、阅读和评审 Harbor 任务与 rollout 产物的协作平台。任务说明、运行轨迹、验证结果和文件保存在同一处；登录用户可以提出修改意见，并独立评价任务是否合格。

Agent 接入指南位于网站左侧底部的「API 接口」，也可访问 `/?project=PROJECT_ID&view=api`。该页面顶部可直接生成 API Key，并包含 Agent 接入所需的全部接口说明与示例；文本版见 [Agent API 文档](docs/AGENT_API.md)。

## 功能

- 项目任务库：按「项目 → 任务集 → Harbor 任务」组织内容，所有登录用户均可新建项目与任务集。
- 文件导入：上传单个 ZIP 或任务目录，任务名称自动采用 ZIP 文件名或目录名，简介可选。
- 产物阅读：桌面任务库常驻项目导航，进入任务后切换为常驻文件目录；手机使用抽屉切换文件。上传包包含 Rollout 结果时展示已有轨迹、验证结果和产物，没有结果时只展示任务内容。
- 文件下载：在文件目录里右键单个文件即可下载原文件，右键文件夹打包成 ZIP 下载；目录顶部的按钮打包当前全部文件。查看 Rollout 结果时，打包范围是该 Rollout 的文件。
- 任务标签：上传时从物理、化学、生物、医学、人工智能、具身智能、编程中选择，也可自定义；任务库可按标签筛选。
- 协作评审：注册、登录、发表评论，提交合格或需修改评价。
- 删除任务：任务作者或管理员可在任务页删除自己上传的任务，网页与 API 均可操作。
- 删除任务集：任务集创建者或管理员可在任务集列表删除空任务集；集合内还有任务时会拒绝，默认任务集不可删除。
- 实时翻译：通过服务端接入 DeepSeek 或兼容 OpenAI Chat Completions 的 API，流式显示译文。
- 管理设置：平台统一提供翻译服务，无需用户配置密钥。
- Agent API：在「API 接口」页面生成 API Key，上传本地任务及已有产物，返回质检链接；支持标签、重试去重、按标签查询与删除任务。一把 Key 适用于账号可访问的全部项目，每次调用指定 `project_id`。
- 暗黑模式：右上角切换浅色与深色，记住本机选择；默认使用浅色。

后端使用 FastAPI 和 SQLite，前端使用 React、TypeScript、Vite。上传的脚本和容器配置作为文件保存，平台不会执行 Harbor 任务。生产环境由 FastAPI 同时提供 API 和构建后的前端。

## 本地运行

需要 Node.js 22、Python 3.12 或更新版本。

```bash
cp deploy/runtime.env .env
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
npm ci
```

开发时分别在两个终端运行：

```bash
# 终端一：API
.venv/bin/uvicorn server.main:app --reload --host 127.0.0.1 --port 8000 --env-file .env
```

```bash
# 终端二：前端
npm run dev
```

打开前端终端显示的网址。Vite 将 `/api` 请求转发给本地后端。

要以单一服务运行生产构建：

```bash
npm run build
.venv/bin/uvicorn server.main:app --host 0.0.0.0 --port 8000 --env-file .env
```

访问 <http://localhost:8000>。同一网络中的其他用户可以通过主机的 IP 和 `8000` 端口访问；外网访问需要部署到可访问的服务器。

## Docker 运行

```bash
git clone git@github.com:WishingCat/Harbor-Dashboard.git
cd Harbor-Dashboard
docker compose up --build -d
```

访问 `http://服务器IP:10323`（本机为 <http://localhost:10323>）。`compose.yaml` 将容器的 8000 端口发布到主机的 10323 端口，改端口只需修改该映射。首次启动会从仓库中的部署快照恢复 133 个账户、2 个项目和 1 个任务集，可直接使用原用户名和密码登录；快照不含任务，任务库从空开始。后端会读取 `deploy/runtime.env` 中的翻译配置。需要自定义配置时，复制该文件为 `.env`，然后运行 `HARBOR_ENV_FILE=.env docker compose up --build -d`。

镜像会先构建前端，再以非 root 用户启动后端。`harbor-data` 命名卷保存数据库与上传文件，重新构建镜像或重启容器后仍保留。

```bash
docker compose logs -f
docker compose down
```

`docker compose down` 保留数据卷；不要在需要保留数据时添加 `--volumes`。

## 首次登录与项目数据

登录页支持用户名或邮箱。导入的团队账号可直接使用姓名拼音登录，密码与导入时的用户名相同；界面显示飞书姓名。原有邮箱账号继续有效。

尚未导入任何账号的全新部署，也可以通过注册页填写姓名、邮箱和密码；**数据库中的第一个注册账号自动成为管理员**。导入团队账号时，可通过下方命令指定管理员。登录后可以阅读任务和参与评审。

### 导入团队账号

准备 UTF-8 CSV，表头为 `name,username`，每行填写成员姓名及已核对的用户名。用户名使用小写字母和数字；中文姓名按拼音连写，同名成员可附加其英文名。导入程序不请求飞书权限，也不保存飞书凭证。

```bash
# 预览，不写入数据库
.venv/bin/python -m server.provision_users --input output/accounts/unipat-users.csv --source unipat-ai

# 备份数据库后执行导入，并指定新账号中的管理员
.venv/bin/python -m server.provision_users --input output/accounts/unipat-users.csv --source unipat-ai --admin-username youradmin --apply
```

将 `youradmin` 替换为 CSV 中需要设为管理员的用户名。初始密码与 CSV 中的用户名相同，后端只存储加盐哈希。重复导入同一来源、同一姓名会跳过已有账号，不重置密码；重复姓名、用户名或现存账号冲突会使整批导入失败。成员没有提供邮箱时，程序使用内部 `.invalid` 占位地址，不发送邮件。原始导入名单保存在被 Git 忽略的 `output/accounts/` 中；本私有仓库通过 `deploy/bootstrap/` 数据库快照迁移账号，无需再次导入。密码以原有加盐哈希保存，登录密码不变。

### 项目数据

工作区初始提供两个项目，登录用户可继续新建项目及其下属任务集：

| 项目 | 初始任务 |
| --- | --- |
| ProjectAA | 空，含「默认任务集」 |
| PaperBenchX | 空 |

侧栏选择项目后，任务库先展示该项目的任务集，进入任务集后上传、搜索和评审 Harbor 任务。新建项目和任务集只需填写名称；每个任务集可持续上传多个任务。上传弹窗显示目标项目与任务集，分享链接携带两级归属；Rollout 和评审归属于对应任务。项目与任务集沿用公开阅读、登录后创建和操作的权限，翻译服务由平台统一提供。

升级时，已有任务迁入所属项目的「默认任务集」，空项目保持空；旧任务链接仍可访问。Agent 上传可指定 `--task-set SET_ID`，旧 API 调用省略任务集时自动归入默认任务集。详细调用见 [Agent API 文档](docs/AGENT_API.md)。

全新数据库默认初始化两道临时任务，文件采用 Harbor 目录格式，不生成虚构的运行记录或评审；首次启动前设置 `HARBOR_SEED_DEMO=false` 可跳过。本仓库的部署快照已标记初始化完成且不含任务，因此从快照恢复的部署不会再生成它们。初始化是幂等的，重启不会重复添加。升级时，原无项目任务归入 ProjectAA；旧的系统演示数据会替换为这两个临时任务，用户创建或已经参与评审的内容保留。

## 上传 Harbor 任务

单步骤任务通常具有以下结构；`solution/` 可以省略，环境也可以通过 `task.toml` 指定预构建镜像。详见 [Harbor 任务结构](https://www.harborframework.com/docs/tasks)。

```text
my-task/
├── task.toml
├── instruction.md
├── environment/
│   └── Dockerfile
├── tests/
│   └── test.sh
└── solution/
    └── solve.sh
```

每次导入一道任务，包内须包含唯一的有效 task.toml，以及同级 instruction.md 或 steps 下的步骤说明。缺少清单、无效 TOML 或包含多道任务时会提示修正。

先在侧栏选择目标项目，进入目标任务集，再上传任务目录或保留该结构的 ZIP。例如在任务的上一级目录打包：

```bash
zip -r my-task.zip my-task
```

推荐将完整任务上传，便于评审者同时阅读说明、验证脚本与参考解。上传弹窗可以选择标签（物理、化学、生物、医学、人工智能、具身智能、编程，也可自定义），不选则沿用任务包 `task.toml` 中 `[metadata] tags` 声明的标签；任务库按标签筛选。Windows 任务使用 `test.bat` / `solve.bat`。[多步骤任务](https://www.harborframework.com/docs/tasks/multi-step)将说明放在 `steps/<step-name>/instruction.md`，上传时应保留整个 `steps/` 目录。

### 随任务导入已有 Rollout 结果

Harbor 的 job 通常包含多个 trial，每个 trial 表示某个 agent 对某道任务的一次运行。典型输出如下，参见 [官方运行结果说明](https://www.harborframework.com/docs/run-jobs/run-evals)：

```text
jobs/my-job/
├── config.json
├── result.json
└── my-task__trial-id/
    ├── config.json
    ├── result.json
    ├── agent/
    │   └── trajectory.json
    ├── verifier/
    │   ├── reward.txt
    │   └── test-stdout.txt
    └── artifacts/
        └── report.md
```

将已有 trial 或 job 放在任务包的 `rollouts/` 或 `jobs/` 目录中，与任务一起上传。平台自动展示可识别的结果，支持切换多个 trial 并阅读轨迹及产物。没有结果的任务只显示任务文件。

任务文件和结果文件分别归档；job 配置、汇总及公共文件也会保留供阅读。

导入读取 `result.json` 中的任务名、agent、模型、时间和 verifier rewards；`agent/trajectory.json` 遵循 [ATIF 格式](https://www.harborframework.com/docs/agents/trajectory-format)。原始文件一并保留，以便核对。没有可解析结果的文件仍可以作为任务材料阅读。

**自动验证奖励与人工评审是两个概念。** `reward.txt` 是一个数值，`reward.json` 可包含多个命名分数；奖励不一定是 0 或 1。人工评价用于判断任务说明、测试和产物是否达到评审要求。

同一评审者可以重新提交评价，以其最近一次决策为准。只要仍有评审者提出“需修改”，任务就显示“需修改”；否则有合格评价时显示“合格”，尚无决策时显示“待评审”。单独发表评论不会覆盖之前的决策。

单次上传上限 50 MB，展开后总计上限 100 MB，单文件上限 20 MB。ZIP 展开后最多 2,000 个文件；直接多文件上传（包括网页目录上传）最多 1,000 个文件，更多文件请使用 ZIP 或 Python 客户端。平台拒绝越界路径，不执行上传文件。

## Agent 上传 API

网站左侧底部的「API 接口」页面提供接入示例，地址为 `/?project=PROJECT_ID&view=api`。API 与网页运行在同一个服务中。在该页面顶部的「生成 API Key」区域即可生成、查看和撤销 Key，明文只显示一次；没有浏览器时的 HTTP 流程见 [获取 API Key](docs/AGENT_API.md#1-获取-api-key)。

一把 Key 适用于账号可访问的全部项目，因此每次调用都要指定目标项目，省略会返回 `422` 并列出可用项目。配置 Agent 的平台地址、Key 与目标项目：

```bash
export HARBOR_API_URL='http://localhost:10323'
export HARBOR_API_TOKEN='hbr_替换为你生成的APIKey'
export HARBOR_PROJECT_ID='project-aa'

python3 scripts/harbor_upload.py task /path/to/my-task \
  --project "$HARBOR_PROJECT_ID" \
  --tag 物理 --tag 人工智能 \
  --rollout /path/to/existing-trial
```

也可以直接上传 ZIP、追加已有结果，或读取人工质检状态：

```bash
python3 scripts/harbor_upload.py projects
python3 scripts/harbor_upload.py task /path/to/my-task.zip --project "$HARBOR_PROJECT_ID"
python3 scripts/harbor_upload.py rollout TASK_ID /path/to/existing-job --project "$HARBOR_PROJECT_ID"
python3 scripts/harbor_upload.py status TASK_ID --project "$HARBOR_PROJECT_ID" --json
python3 scripts/harbor_upload.py tags --project "$HARBOR_PROJECT_ID"
python3 scripts/harbor_upload.py tasks --project "$HARBOR_PROJECT_ID" --tag 物理
python3 scripts/harbor_upload.py delete TASK_ID --project "$HARBOR_PROJECT_ID"
```

上传输出包含质检页面链接，加 `--json` 可读取 `task_url` 字段，直接分享给评审者。客户端无需第三方依赖；两个上传接口均支持重试去重。部署后可从平台 `/api/agent-client.py` 下载客户端。

打包下载接口为 `GET /api/tasks/{task_id}/archive`，可选 `prefix`（文件夹路径，省略则打包全部）和 `rollout_id`（打包某个 Rollout 的文件）。它与单文件下载 `GET /api/files/{file_id}/download` 一样属于公开阅读接口，不需要 Token。

接口包括 `/api/v1/projects`、`/api/v1/task-sets`、`/api/v1/tags`、`/api/v1/tasks`、`/api/v1/tasks/{task_id}`（GET 与 DELETE）和 `/api/v1/tasks/{task_id}/rollouts`，通过 `Authorization: Bearer KEY` 认证。交互式文档在 `/docs`，OpenAPI 定义在 `/openapi.json`。完整参数、curl 示例、权限与错误处理见站内「API 接口」页面或 [Agent API 使用说明](docs/AGENT_API.md)。

部署到服务器后，将 `HARBOR_API_URL` 改为服务器地址。**共享部署必须配置 `HARBOR_PUBLIC_URL`**：留空时，API 返回的 `task_url` 按请求的 `Host` 头生成，而该头由调用方决定——上传者可以让平台返回一个指向任意域名的质检链接，再借评审流程把它分发出去。配置后链接被固定，`Host` 头不再起作用。仅本机使用时可以留空。

## 平台统一翻译

翻译由平台统一提供，登录用户可直接在文档中开启“双语阅读”，无需个人 API Key。设置页在托管模式下只显示服务摘要，管理员也不通过网页修改路由或密钥。

本私有仓库的实际网关地址、模型和密钥保存在 `deploy/runtime.env`，Docker Compose 自动读取。该文件已排除于 Docker 构建上下文，密钥只注入后端进程。自定义服务器配置可以复制为 `.env` 并通过 `HARBOR_ENV_FILE=.env` 选择；非 Docker 启动使用 `--env-file deploy/runtime.env`。

```dotenv
TRANSLATION_MANAGED=true
TRANSLATION_PROVIDER=DeepSeek
TRANSLATION_API_KEY=your-server-api-key
TRANSLATION_BASE_URL=https://your-gateway.example/v1
TRANSLATION_MODEL=deepseek-flash
TRANSLATION_THINKING=disabled
TRANSLATION_TRUST_ENV=false
```

服务端向 Base URL 的 `/chat/completions` 发起流式请求，携带 `thinking: {"type": "disabled"}`。服务器环境变量可以指定 HTTP 或 HTTPS 网关；网页配置接口仍保留 HTTPS 限制。`TRANSLATION_TRUST_ENV=false` 使网关请求直连，设为 `true` 时使用服务器的 HTTP(S)/SOCKS 代理环境配置。

托管模式只读取服务器环境配置，数据库中的旧提供商配置不会覆盖它。API Key 不返回给浏览器，也不打包进前端或镜像；修改部署环境文件后，执行 `docker compose up -d --force-recreate` 重新加载；非 Docker 部署重启后端生效。文档文本通过后端发送到该网关，译文流式返回页面。

如需恢复旧版的管理员网页配置，可设置 `TRANSLATION_MANAGED=false`。该模式支持在设置页配置服务端 API Key；切换 API 地址必须填写新密钥。

## 数据与部署

| 配置项 | 默认值或用途 |
| --- | --- |
| `HARBOR_DATA_DIR` | 本地为 `./data`；Docker 中为 `/app/data` |
| `HARBOR_BOOTSTRAP_DIR` | 首次启动的数据快照目录；已有数据库时跳过恢复 |
| `HARBOR_SEED_DEMO` | 全新数据库是否初始化 ProjectAA 的两道临时任务，默认 `true`；从部署快照恢复时不生效 |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `TRANSLATION_API_KEY` | 平台统一翻译密钥，仅服务端使用 |
| `TRANSLATION_MANAGED` | 示例配置为 `true`；禁止网页修改平台服务 |
| `TRANSLATION_PROVIDER` | 平台服务名称，例如 `DeepSeek` |
| `TRANSLATION_BASE_URL` | `https://api.deepseek.com` |
| `TRANSLATION_MODEL` | 当前配置为 `deepseek-flash` |
| `TRANSLATION_THINKING` | `disabled`：关闭模型思考输出 |
| `TRANSLATION_TRUST_ENV` | `false` 直连；`true` 使用服务器代理环境变量 |
| `HARBOR_PUBLIC_URL` | 平台对外基础地址，用于生成 API 返回的质检链接；共享部署必须设置，留空时链接跟随请求的 `Host` 头 |
| `COOKIE_SECURE` | 本地 HTTP 使用 `false`，生产 HTTPS 使用 `true` |

数据库文件为数据目录中的 `harbor.sqlite3`。备份时停止服务后复制**整个数据目录**，同时保存数据库与上传文件；恢复时将完整备份放回相同数据目录。本私有仓库按部署要求跟踪 `deploy/bootstrap/`（用户密码哈希、任务与文件）和 `deploy/runtime.env`（后端配置及翻译密钥）。运行时的 `data/`、个人 `.env`、日志和缓存仍不跟踪。仓库及含账户快照的镜像应保持私有。

快照仅用于**空数据卷的首次初始化**。已有数据库时完全跳过恢复，因此重新部署不会重置密码或覆盖后续上传。原站的浏览器登录会话未迁移，用户需要在新站重新登录。仓库快照不是运行时自动备份；后续数据仍需备份完整数据目录。更新快照时，先停止服务，再运行 `python -m server.deployment --source data --destination deploy/bootstrap-next`，核对后替换 `deploy/bootstrap/` 并提交。

公开部署时，在 FastAPI 前使用提供 HTTPS 的反向代理，将 `COOKIE_SECURE=true`，现有管理员账号随快照保留。代理将网站请求转发到容器的 `8000` 端口，保留浏览器请求的 `Host` 头，并为翻译请求关闭响应缓冲，以便及时显示译文。`/api/health` 可用于健康检查，Docker 镜像已配置该检查。SQLite 与本地文件存储适合单实例部署；多实例共享、对象存储、邮件找回密码和企业 SSO 需要后续扩展。

## 项目结构

```text
src/                 React 前端
server/              FastAPI API、导入与数据存储
scripts/             Agent 上传客户端
deploy/              私有部署快照与后端运行配置
docs/                API 使用说明
data/                本地数据库与上传文件（运行时生成）
dist/                前端生产构建（构建时生成）
.env.example         环境变量示例
Dockerfile           多阶段生产镜像
compose.yaml         单实例启动与持久化卷
```

## 检查

```bash
npm run typecheck
npm run build
.venv/bin/python -m pytest -q tests
```
