# Harbor Rollout Dashboard

用于上传、阅读和评审 Harbor 任务与 rollout 产物的协作平台。任务说明、运行轨迹、验证结果和文件保存在同一处；登录用户可以提出修改意见，并独立评价任务是否合格。

Agent 接入指南位于网站左侧底部的「API 接口」，也可访问 `/?project=PROJECT_ID&view=api`。该页面提供调用说明和示例；项目 Token 通过 HTTP 获取，详见 [Agent API 文档](docs/AGENT_API.md)。

## 功能

- 项目任务库：按「项目 → 任务集 → Harbor 任务」组织内容，所有登录用户均可新建项目与任务集。
- 文件导入：上传单个 ZIP 或任务目录，任务名称自动采用 ZIP 文件名或目录名，简介可选。
- 产物阅读：桌面任务库常驻项目导航，进入任务后切换为常驻文件目录；手机使用抽屉切换文件。上传包包含 Rollout 结果时展示已有轨迹、验证结果和产物，没有结果时只展示任务内容。
- 协作评审：注册、登录、发表评论，提交合格或需修改评价。
- 实时翻译：通过服务端接入 DeepSeek 或兼容 OpenAI Chat Completions 的 API，流式显示译文。
- 管理设置：平台统一提供翻译服务，无需用户配置密钥。
- Agent API：使用项目 Token 上传本地任务及已有产物，返回质检链接；支持重试去重与查询评审状态。
- 暗黑模式：右上角切换浅色与深色，记住本机选择；首次使用跟随系统主题。

后端使用 FastAPI 和 SQLite，前端使用 React、TypeScript、Vite。上传的脚本和容器配置作为文件保存，平台不会执行 Harbor 任务。生产环境由 FastAPI 同时提供 API 和构建后的前端。

## 本地运行

需要 Node.js 22、Python 3.12 或更新版本。

```bash
cp .env.example .env
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
cp .env.example .env
docker compose up --build -d
```

访问 <http://localhost:8000>。镜像会先构建前端，再以非 root 用户启动后端。`harbor-data` 命名卷保存数据库与上传文件，重新构建镜像或重启容器后仍保留。

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

将 `youradmin` 替换为 CSV 中需要设为管理员的用户名。初始密码与 CSV 中的用户名相同，后端只存储加盐哈希。重复导入同一来源、同一姓名会跳过已有账号，不重置密码；重复姓名、用户名或现存账号冲突会使整批导入失败。成员没有提供邮箱时，程序使用内部 `.invalid` 占位地址，不发送邮件。实际名单保存在被 Git 忽略的 `output/accounts/` 中，部署时通过数据库备份迁移账号。

### 项目数据

工作区初始提供两个项目，登录用户可继续新建项目及其下属任务集：

| 项目 | 初始任务 |
| --- | --- |
| ProjectAA | 默认任务集中有两道临时任务 |
| PaperBenchX | 空 |

侧栏选择项目后，任务库先展示该项目的任务集，进入任务集后上传、搜索和评审 Harbor 任务。新建项目和任务集只需填写名称；每个任务集可持续上传多个任务。上传弹窗显示目标项目与任务集，分享链接携带两级归属；Rollout 和评审归属于对应任务。项目与任务集沿用公开阅读、登录后创建和操作的权限，翻译服务由平台统一提供。

升级时，已有任务迁入所属项目的「默认任务集」，空项目保持空；旧任务链接仍可访问。Agent 上传可指定 `--task-set SET_ID`，旧 API 调用省略任务集时自动归入默认任务集。详细调用见 [Agent API 文档](docs/AGENT_API.md)。

默认初始化两个临时任务，文件采用 Harbor 目录格式，不生成虚构的运行记录或评审。首次启动前设置 `HARBOR_SEED_DEMO=false` 可保留两个空项目。初始化是幂等的，重启不会重复添加。升级时，原无项目任务归入 ProjectAA；旧的系统演示数据会替换为这两个临时任务，用户创建或已经参与评审的内容保留。

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

推荐将完整任务上传，便于评审者同时阅读说明、验证脚本与参考解。Windows 任务使用 `test.bat` / `solve.bat`。[多步骤任务](https://www.harborframework.com/docs/tasks/multi-step)将说明放在 `steps/<step-name>/instruction.md`，上传时应保留整个 `steps/` 目录。

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

网站左侧底部的「API 接口」页面提供接入示例，地址为 `/?project=PROJECT_ID&view=api`。API 与网页运行在同一个服务中。使用登录会话调用 `POST /api/auth/tokens` 获取绑定项目的 Token，具体步骤见 [获取项目 Token](docs/AGENT_API.md#1-获取项目-token)。然后配置 Agent 的平台地址和 Token：

```bash
export HARBOR_API_URL='http://localhost:5173'
export HARBOR_API_TOKEN='hbr_替换为你的项目Token'

python3 scripts/harbor_upload.py task /path/to/my-task \
  --rollout /path/to/existing-trial
```

也可以直接上传 ZIP、追加已有结果，或读取人工质检状态：

```bash
python3 scripts/harbor_upload.py task /path/to/my-task.zip
python3 scripts/harbor_upload.py rollout TASK_ID /path/to/existing-job
python3 scripts/harbor_upload.py status TASK_ID --json
```

上传输出包含质检页面链接，加 `--json` 可读取 `task_url` 字段，直接分享给评审者。客户端无需第三方依赖；两个上传接口均支持重试去重。部署后可从平台 `/api/agent-client.py` 下载客户端。

接口为 `/api/v1/tasks`、`/api/v1/tasks/{task_id}` 和 `/api/v1/tasks/{task_id}/rollouts`，通过 `Authorization: Bearer TOKEN` 认证。交互式文档在 `/docs`，OpenAPI 定义在 `/openapi.json`。完整参数、curl 示例、权限与错误处理见 [Agent API 使用说明](docs/AGENT_API.md)。

当前保留本地运行。之后部署到服务器时，将 `HARBOR_API_URL` 改为服务器地址，并配置 `HARBOR_PUBLIC_URL` 生成对外质检链接。

## 平台统一翻译

翻译由平台统一提供，登录用户可直接在文档中开启“双语阅读”，无需个人 API Key。设置页在托管模式下只显示服务摘要，管理员也不通过网页修改路由或密钥。

实际网关地址、模型和密钥保存于服务器的 `.env`（已被 Git 和 Docker 构建上下文排除）。部署到另一台服务器时复制该文件或注入对应环境变量，并通过 `--env-file .env` 启动；Docker Compose 已配置读取 `.env`。

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

托管模式只读取服务器环境配置，数据库中的旧提供商配置不会覆盖它。API Key 不返回给浏览器，也不打包进前端或镜像；修改 `.env` 后重启后端生效。文档文本通过后端发送到该网关，译文流式返回页面。

如需恢复旧版的管理员网页配置，可设置 `TRANSLATION_MANAGED=false`。该模式支持在设置页配置服务端 API Key；切换 API 地址必须填写新密钥。

## 数据与部署

| 配置项 | 默认值或用途 |
| --- | --- |
| `HARBOR_DATA_DIR` | 本地为 `./data`；Docker 中为 `/app/data` |
| `HARBOR_SEED_DEMO` | 是否初始化 ProjectAA 的两道临时任务，默认 `true` |
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 |
| `TRANSLATION_API_KEY` | 平台统一翻译密钥，仅服务端使用 |
| `TRANSLATION_MANAGED` | 示例配置为 `true`；禁止网页修改平台服务 |
| `TRANSLATION_PROVIDER` | 平台服务名称，例如 `DeepSeek` |
| `TRANSLATION_BASE_URL` | `https://api.deepseek.com` |
| `TRANSLATION_MODEL` | 当前配置为 `deepseek-flash` |
| `TRANSLATION_THINKING` | `disabled`：关闭模型思考输出 |
| `TRANSLATION_TRUST_ENV` | `false` 直连；`true` 使用服务器代理环境变量 |
| `HARBOR_PUBLIC_URL` | 可选的平台对外基础地址，用于生成 API 返回的质检链接 |
| `COOKIE_SECURE` | 本地 HTTP 使用 `false`，生产 HTTPS 使用 `true` |

数据库文件为数据目录中的 `harbor.sqlite3`。备份时停止服务后复制**整个数据目录**，同时保存数据库与上传文件；恢复时将完整备份放回相同数据目录。不要将 `.env`、数据目录或实际 API 密钥提交到 Git。

公开部署时，在 FastAPI 前使用提供 HTTPS 的反向代理，将 `COOKIE_SECURE=true`，并完成管理员注册。代理将网站请求转发到容器的 `8000` 端口，保留浏览器请求的 `Host` 头，并为翻译请求关闭响应缓冲，以便及时显示译文。`/api/health` 可用于健康检查，Docker 镜像已配置该检查。SQLite 与本地文件存储适合单实例部署；多实例共享、对象存储、邮件找回密码和企业 SSO 需要后续扩展。

## 项目结构

```text
src/                 React 前端
server/              FastAPI API、导入与数据存储
scripts/             Agent 上传客户端
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
