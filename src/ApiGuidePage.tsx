import { useEffect, useId, useRef, useState } from 'react';
import { Check, ChevronDown, Copy, Download, ExternalLink, KeyRound, LoaderCircle, Trash2 } from 'lucide-react';
import { api, post } from './api';
import type { ApiToken, Project, TaskSet, User } from './types';
import { PRESET_TAGS } from './tags';
import './api-guide.css';

// Values shown in examples remain shell data, including quotes in imported IDs.
const shellQuote = (value: string) => "'" + value.replace(/'/g, "'\"'\"'") + "'";

function CodeExample({title, code, notify}: {title: string; code: string; notify: (message: string) => void}) {
  const [copied, setCopied] = useState(false);
  const pre = useRef<HTMLPreElement>(null);
  const currentCode = useRef(code);
  currentCode.current = code;
  useEffect(() => {setCopied(false);}, [code]);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1800);
    return () => clearTimeout(timer);
  }, [copied]);
  async function copy() {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(code);
      if (currentCode.current !== code) return;
      setCopied(true); notify('示例已复制');
    } catch {
      if (currentCode.current !== code) return;
      const selection = window.getSelection();
      if (pre.current && selection) {
        pre.current.focus({preventScroll: true});
        const range = document.createRange();
        range.selectNodeContents(pre.current);
        selection.removeAllRanges(); selection.addRange(range);
        notify('自动复制不可用，已选中代码，可手动复制。');
      } else notify('自动复制不可用，请手动选择代码复制。');
    }
  }
  return <div className="api-guide-code"><div className="api-guide-code-heading"><span>{title}</span><button type="button" onClick={() => void copy()} aria-label={`复制${title}`}>{copied ? <Check size={15} /> : <Copy size={15} />}{copied ? '已复制' : '复制'}</button></div><pre ref={pre} tabIndex={0} aria-label={title}><code>{code}</code></pre></div>;
}

const EXPIRY_CHOICES = [30, 90, 365];

function tokenState(token: ApiToken) {
  if (token.revoked) return {label: '已撤销', className: 'revoked'};
  if (new Date(token.expires_at).getTime() <= Date.now()) return {label: '已过期', className: 'revoked'};
  return {label: '可用', className: 'active'};
}

const shortDate = (value: string) => new Date(value).toLocaleDateString('zh-CN', {year: 'numeric', month: 'short', day: 'numeric'});

/** Mint a project Token in the browser so an Agent needs no login credentials. */
function TokenManager({project, projects, user, origin, onLogin, notify}: {project: Project; projects: Project[]; user: User | null; origin: string; onLogin: () => void; notify: (message: string) => void}) {
  const [tokens, setTokens] = useState<ApiToken[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [name, setName] = useState('Agent upload');
  const [expiry, setExpiry] = useState(EXPIRY_CHOICES[0]);
  const [busy, setBusy] = useState(false);
  const [secret, setSecret] = useState('');
  const [error, setError] = useState('');
  const [confirming, setConfirming] = useState('');
  const projectName = (id: string) => projects.find(item => item.id === id)?.name || id;

  // Revoking cannot be undone, so the button arms first and disarms on its own.
  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(''), 5000);
    return () => clearTimeout(timer);
  }, [confirming]);

  useEffect(() => {
    if (!user) {setTokens([]); setLoaded(true); return;}
    let alive = true;
    setLoaded(false);
    api<{tokens: ApiToken[]}>('/auth/tokens')
      .then(result => {if (alive) {setTokens(result.tokens); setError('');}})
      .catch(e => {if (alive) setError((e as Error).message);})
      .finally(() => {if (alive) setLoaded(true);});
    return () => {alive = false;};
  }, [user]);

  async function create() {
    if (busy) return;
    if (!name.trim()) {setError('请填写 Token 名称，便于以后识别和撤销。'); return;}
    setBusy(true); setError('');
    try {
      const result = await post<{token: string; api_token: ApiToken}>('/auth/tokens', {name: name.trim(), project_id: project.id, expires_in_days: expiry});
      setSecret(result.token);
      setTokens(current => [result.api_token, ...current]);
      notify('Token 已生成');
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }
  async function revoke(token: ApiToken) {
    if (busy) return;
    if (confirming !== token.id) {setConfirming(token.id); return;}
    setConfirming(''); setBusy(true); setError('');
    try {
      await api(`/auth/tokens/${encodeURIComponent(token.id)}`, {method: 'DELETE'});
      setTokens(current => current.map(item => item.id === token.id ? {...item, revoked: true, revoked_at: new Date().toISOString()} : item));
      notify('Token 已撤销');
    } catch (e) {setError((e as Error).message);}
    finally {setBusy(false);}
  }

  if (!user) return <div className="token-panel"><p>登录后可以直接生成项目 Token，无需在终端里输入账号密码。</p><button type="button" className="button primary" onClick={onLogin}><KeyRound size={15} />登录并生成 Token</button></div>;
  return <div className="token-panel">
    <div className="token-form">
      <label>Token 名称<input value={name} maxLength={80} disabled={busy} onChange={event => setName(event.target.value)} placeholder="例如 Agent upload" /></label>
      <label>有效期<select value={expiry} disabled={busy} onChange={event => setExpiry(Number(event.target.value))}>{EXPIRY_CHOICES.map(days => <option key={days} value={days}>{days} 天</option>)}</select></label>
      <button type="button" className="button primary" disabled={busy} onClick={() => void create()}>{busy ? <LoaderCircle size={15} className="spin" /> : <KeyRound size={15} />}生成 Token</button>
    </div>
    <p className="api-guide-note">Token 绑定当前项目 <strong>{project.name}</strong>，代表你的账号上传。切换页面顶部的目标项目即可为其他项目生成。</p>
    {error && <p className="form-error" role="alert">{error}</p>}
    {secret && <div className="token-reveal">
      <div className="token-reveal-heading"><KeyRound size={15} /><strong>Token 已生成，只显示这一次</strong></div>
      <CodeExample title="复制到 Agent 的环境变量" code={`export HARBOR_API_URL=${shellQuote(origin)}\nexport HARBOR_API_TOKEN=${shellQuote(secret)}`} notify={notify} />
      <p className="api-guide-note">离开或刷新页面后无法再次查看。丢失时撤销旧 Token 并重新生成即可，不需要重置账号密码。</p>
    </div>}
    {!loaded ? <p className="api-guide-note">正在读取已有 Token…</p>
      : tokens.length === 0 ? <p className="api-guide-note">还没有生成过 Token。</p>
      : <div className="api-guide-table-wrap"><table className="token-table"><thead><tr><th>名称</th><th>项目</th><th>状态</th><th>到期</th><th>最近使用</th><th aria-label="撤销" /></tr></thead><tbody>{tokens.map(token => {
          const state = tokenState(token);
          return <tr key={token.id}><td>{token.name}</td><td>{projectName(token.project_id)}</td><td><span className={`token-state ${state.className}`}>{state.label}</span></td><td>{shortDate(token.expires_at)}</td><td>{token.last_used_at ? shortDate(token.last_used_at) : '未使用'}</td><td>{!token.revoked && <button type="button" className={`token-revoke ${confirming === token.id ? 'armed' : ''}`} aria-label={confirming === token.id ? `确认撤销 ${token.name}` : `撤销 ${token.name}`} title="撤销后使用它的 Agent 立即失效" disabled={busy} onClick={() => void revoke(token)}>{confirming === token.id ? '确认撤销' : <Trash2 size={15} />}</button>}</td></tr>;
        })}</tbody></table></div>}
  </div>;
}

const endpoints = [
  ['GET', '/api/v1/projects', 'Token 所属项目'],
  ['GET', '/api/v1/task-sets', '列出任务集'],
  ['POST', '/api/v1/task-sets', '新建任务集'],
  ['GET', '/api/v1/tags', '预设标签词表与已用标签'],
  ['GET', '/api/v1/tasks', '列出任务，可按任务集与标签筛选'],
  ['POST', '/api/v1/tasks', '上传任务及包内已有结果'],
  ['GET', '/api/v1/tasks/{task_id}', '任务、文件、结果与评审'],
  ['POST', '/api/v1/tasks/{task_id}/rollouts', '追加已有产物'],
];

export default function ApiGuidePage({project, projects, taskSets, user, onProjectChange, onLogin, notify}: {project: Project; projects: Project[]; taskSets: TaskSet[]; user: User | null; onProjectChange: (projectId: string) => void; onLogin: () => void; notify: (message: string) => void}) {
  const [method, setMethod] = useState<'python' | 'curl'>('python');
  const [choice, setChoice] = useState({projectId: project.id, taskSetId: taskSets.find(item => item.project_id === project.id)?.id || ''});
  const sets = taskSets.filter(item => item.project_id === project.id);
  const selectedId = choice.projectId === project.id && (!choice.taskSetId || sets.some(item => item.id === choice.taskSetId)) ? choice.taskSetId : sets[0]?.id || '';
  const origin = location.origin;
  const methodPanelId = useId();
  const environment = `export HARBOR_API_URL=${shellQuote(origin)}\nexport HARBOR_PROJECT_ID=${shellQuote(project.id)}`;
  const tokenBody = shellQuote(JSON.stringify({name: 'Agent upload', project_id: project.id, expires_in_days: 30}));
  const authentication = `${environment}
unset HARBOR_API_TOKEN
harbor_auth() {
  local harbor_cookie harbor_token harbor_status
  harbor_cookie="$(mktemp)" || return
  harbor_token="$(mktemp)" || { rm -f -- "$harbor_cookie"; return 1; }
  python3 -c 'import getpass,json; print(json.dumps({"username":getpass.getpass("用户名或邮箱: "),"password":getpass.getpass("密码: ")}))' |
  curl --fail-with-body --silent --show-error \\
    "$HARBOR_API_URL/api/auth/login" \\
    -c "$harbor_cookie" -H 'Content-Type: application/json' \\
    --data-binary @- -o /dev/null &&
  curl --fail-with-body --silent --show-error \\
    "$HARBOR_API_URL/api/auth/tokens" \\
    -b "$harbor_cookie" -H 'Content-Type: application/json' \\
    -d ${tokenBody} -o "$harbor_token" &&
  HARBOR_API_TOKEN="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["token"])' "$harbor_token")" &&
  test -n "$HARBOR_API_TOKEN" && export HARBOR_API_TOKEN
  harbor_status=$?
  curl --fail-with-body --silent --show-error \\
    "$HARBOR_API_URL/api/auth/logout" -b "$harbor_cookie" -X POST -o /dev/null
  rm -f -- "$harbor_cookie" "$harbor_token"
  return "$harbor_status"
}
harbor_auth`;
  const prerequisites = `${environment}\nunset HARBOR_TASK_SET_ID HARBOR_TASK_ID\n: "\${HARBOR_API_TOKEN:?请先完成项目 Token 认证}"`;
  const uploadGuard = `: "\${HARBOR_API_URL:?先完成准备步骤}" "\${HARBOR_PROJECT_ID:?先选择项目}" \\
  "\${HARBOR_TASK_SET_ID:?先完成任务集准备}" "\${HARBOR_API_TOKEN:?先完成认证}"`;
  const followupGuard = `${uploadGuard} "\${HARBOR_TASK_ID:?先成功上传任务}"`;
  const setExport = selectedId ? `export HARBOR_TASK_SET_ID=${shellQuote(selectedId)}` : `HARBOR_TASK_SET_ID="$(python3 -c 'import json; print(json.load(open("harbor-task-set.json"))["task_set"]["id"])')" &&
test -n "$HARBOR_TASK_SET_ID" && export HARBOR_TASK_SET_ID`;
  const pythonSetup = `${prerequisites} &&
curl --fail --show-error "$HARBOR_API_URL/api/agent-client.py" -o harbor_upload.py &&
python3 harbor_upload.py task-sets --project "$HARBOR_PROJECT_ID" &&
${selectedId ? '' : `python3 harbor_upload.py create-task-set 'Agent 上传' \\
  --project "$HARBOR_PROJECT_ID" --json > harbor-task-set.json &&
`}${setExport}`;
  const curlSetup = `${prerequisites} &&
curl --fail-with-body --show-error --get \\
  "$HARBOR_API_URL/api/v1/task-sets" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  --data-urlencode "project_id=$HARBOR_PROJECT_ID" &&
${selectedId ? '' : `curl --fail-with-body --show-error \\
  "$HARBOR_API_URL/api/v1/task-sets" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  -H 'Content-Type: application/json' \\
  -d ${shellQuote(JSON.stringify({name: 'Agent 上传', project_id: project.id}))} \\
  -o harbor-task-set.json &&
`}${setExport}`;
  const readUpload = `HARBOR_TASK_ID="$(python3 -c 'import json; print(json.load(open("harbor-task.json"))["task"]["id"])')" &&
test -n "$HARBOR_TASK_ID" && export HARBOR_TASK_ID &&
python3 -c 'import json; print(json.load(open("harbor-task.json"))["task_url"])'`;
  const pythonUpload = `unset HARBOR_TASK_ID
${uploadGuard} &&
python3 harbor_upload.py task '/path/to/my-task' \\
  --project "$HARBOR_PROJECT_ID" --task-set "$HARBOR_TASK_SET_ID" \\
  --tag ${shellQuote(PRESET_TAGS[0])} --tag ${shellQuote(PRESET_TAGS[4])} \\
  --json > harbor-task.json &&
${readUpload}`;
  const curlUpload = `unset HARBOR_TASK_ID
${uploadGuard} &&
HARBOR_UPLOAD_KEY="\${HARBOR_UPLOAD_KEY:-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')}" &&
curl --fail-with-body --show-error \\
  "$HARBOR_API_URL/api/v1/tasks" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  -H "Idempotency-Key: $HARBOR_UPLOAD_KEY" \\
  --form-string "project_id=$HARBOR_PROJECT_ID" \\
  --form-string "task_set_id=$HARBOR_TASK_SET_ID" \\
  --form-string ${shellQuote('tags=' + JSON.stringify([PRESET_TAGS[0], PRESET_TAGS[4]]))} \\
  -F 'files=@/path/to/my-task.zip' -o harbor-task.json &&
${readUpload}`;
  const pythonWithRollout = `unset HARBOR_TASK_ID
${uploadGuard} &&
python3 harbor_upload.py task '/path/to/my-task' \\
  --project "$HARBOR_PROJECT_ID" --task-set "$HARBOR_TASK_SET_ID" \\
  --rollout '/path/to/existing-trial' \\
  --json > harbor-task.json &&
${readUpload}`;
  const tagPython = `${uploadGuard} &&
python3 harbor_upload.py tags --project "$HARBOR_PROJECT_ID" &&
python3 harbor_upload.py tasks --project "$HARBOR_PROJECT_ID" \\
  --task-set "$HARBOR_TASK_SET_ID" --tag ${shellQuote(PRESET_TAGS[0])}`;
  const tagCurl = `${uploadGuard} &&
curl --fail-with-body --show-error --get \\
  "$HARBOR_API_URL/api/v1/tags" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  --data-urlencode "project_id=$HARBOR_PROJECT_ID" &&
curl --fail-with-body --show-error --get \\
  "$HARBOR_API_URL/api/v1/tasks" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  --data-urlencode "project_id=$HARBOR_PROJECT_ID" \\
  --data-urlencode "task_set_id=$HARBOR_TASK_SET_ID" \\
  --data-urlencode ${shellQuote('tag=' + PRESET_TAGS[0])}`;
  const pythonFollowup = `${followupGuard} &&
python3 harbor_upload.py rollout "$HARBOR_TASK_ID" '/path/to/job-results' \\
  --project "$HARBOR_PROJECT_ID" --task-set "$HARBOR_TASK_SET_ID"

${followupGuard} &&
python3 harbor_upload.py status "$HARBOR_TASK_ID" \\
  --project "$HARBOR_PROJECT_ID" --task-set "$HARBOR_TASK_SET_ID" --json`;
  const curlFollowup = `${followupGuard} &&
HARBOR_ROLLOUT_KEY="\${HARBOR_ROLLOUT_KEY:-$(python3 -c 'import uuid; print(uuid.uuid4().hex)')}" &&
curl --fail-with-body --show-error \\
  "$HARBOR_API_URL/api/v1/tasks/$HARBOR_TASK_ID/rollouts" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  -H "Idempotency-Key: $HARBOR_ROLLOUT_KEY" \\
  --form-string "project_id=$HARBOR_PROJECT_ID" \\
  --form-string "task_set_id=$HARBOR_TASK_SET_ID" \\
  -F 'files=@/path/to/results.zip'

${followupGuard} &&
curl --fail-with-body --show-error --get \\
  "$HARBOR_API_URL/api/v1/tasks/$HARBOR_TASK_ID" \\
  -H "Authorization: Bearer $HARBOR_API_TOKEN" \\
  --data-urlencode "project_id=$HARBOR_PROJECT_ID" \\
  --data-urlencode "task_set_id=$HARBOR_TASK_SET_ID"`;

  return <div className="page-content api-guide-page">
    <div className="page-heading"><h1>API 接口</h1></div>
    <div className="api-guide-content">
      <p className="api-guide-intro">上传 Harbor 任务与已有产物，获取团队质检结果。平台保存文件，不执行 Rollout。先选择目标项目，下方 Token 与示例都会随之更新。</p>
      <div className="api-guide-links"><a href="/api/agent-client.py" download="harbor_upload.py"><Download size={15} />下载 Python 客户端</a><a href="/docs" target="_blank" rel="noreferrer">API 文档<ExternalLink size={14} /></a><a href="/openapi.json" target="_blank" rel="noreferrer">OpenAPI<ExternalLink size={14} /></a></div>
      <div className="api-guide-context"><label className="api-guide-project">目标项目<select value={project.id} onChange={event => onProjectChange(event.target.value)}>{projects.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label><code>{origin}</code></div>
      <p className="api-guide-note">基础地址不含 /api。远程 Agent 请将示例地址换为可访问的平台 HTTPS 地址；localhost 仅指运行命令的机器。</p>

      <details className="api-guide-disclosure api-guide-auth">
        <summary><span>1. 生成项目 Token</span><span className="api-guide-summary-note">首次使用</span><ChevronDown size={17} /></summary>
        <div className="api-guide-disclosure-body"><p>在这里直接生成，Agent 只需要这一串 Token 即可上传，不需要平台账号或密码。</p><TokenManager project={project} projects={projects} user={user} origin={origin} onLogin={onLogin} notify={notify} /><p className="api-guide-note">Token 明文仅生成时显示一次，请保存到 Agent 的凭证配置。它只用于所属项目的任务集、任务与产物 API；后续请求使用 Bearer Token，不能用它创建其他 Token，也不能读取平台账号信息。</p><details className="api-guide-nested"><summary>没有浏览器时：在终端里生成<ChevronDown size={16} /></summary><p>下面的命令通过登录 Cookie 创建所选项目的 30 天 Token，并读入 <code>HARBOR_API_TOKEN</code>；账号与密码输入不会回显。</p><CodeExample title="登录并创建项目 Token" code={authentication} notify={notify} /></details></div>
      </details>

      <section className="api-guide-quickstart" aria-labelledby="api-guide-start">
        <div className="api-guide-section-heading"><h2 id="api-guide-start">2. 上传任务</h2><div className="api-guide-methods" role="group" aria-label="调用方式"><button type="button" aria-pressed={method === 'python'} aria-controls={methodPanelId} onClick={() => setMethod('python')}>Python CLI<span>推荐</span></button><button type="button" aria-pressed={method === 'curl'} aria-controls={methodPanelId} onClick={() => setMethod('curl')}>curl</button></div></div>
        <p className="api-guide-note">需要 Python 3.9+ 和 curl。先完成上方认证，再在同一终端依次运行示例；将 /path/to/… 替换为本机文件路径。</p>
        <label className="api-guide-set-selector">目标任务集<select value={selectedId} onChange={event => setChoice({projectId: project.id, taskSetId: event.target.value})}>{sets.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}<option value="">新建任务集：Agent 上传</option></select></label>
        {!selectedId && <p className="api-guide-note">下方先新建“Agent 上传”任务集，再自动读取其 ID。创建断连时先检查列表，避免重复新建。</p>}
        <div id={methodPanelId}>
          <CodeExample title={selectedId ? '准备客户端与任务集' : '准备并新建任务集'} code={method === 'python' ? pythonSetup : curlSetup} notify={notify} />
          <p className="api-guide-step">{method === 'python' ? '上传任务目录或 ZIP；名称取目录名或 ZIP 文件名。' : '上传任务 ZIP。目录上传建议使用 Python CLI。'}</p>
          <p className="api-guide-note">每次上传一个任务根目录，含有效 UTF-8 TOML 格式的 <code>task.toml</code>，以及 <code>instruction.md</code> 或 <code>steps/**/instruction.md</code>。保留 environment、tests 等目录；ZIP 可带一层父目录，不能混装多个任务。</p>
          <CodeExample title="上传并获取任务链接" code={method === 'python' ? pythonUpload : curlUpload} notify={notify} />
          <p className="api-guide-result">命令最后输出 <code>task_url</code>，可直接分享给评审者。任务 ID 已保存为 <code>HARBOR_TASK_ID</code>，用于后续追加与查询。</p>
          <details className="api-guide-nested"><summary>标签：打标与筛选<ChevronDown size={16} /></summary><p>预设词表 {PRESET_TAGS.join(' · ')}，也接受任何自定义标签；每个任务最多 20 个，单个不超过 50 字符。省略标签参数时沿用任务包 <code>task.toml</code> 中声明的标签。</p><CodeExample title={method === 'python' ? '读取词表并按标签筛选' : '读取词表并按标签筛选（curl）'} code={method === 'python' ? tagPython : tagCurl} notify={notify} /><p className="api-guide-note">重复 <code>tag</code> 取交集：任务需同时带上全部标签才会返回。标签参与幂等指纹，同一个 <code>Idempotency-Key</code> 改变标签会返回 <code>409</code>。</p></details>
          {method === 'python' ? <details className="api-guide-nested"><summary>上传时附带已有 Rollout<ChevronDown size={16} /></summary><p>用下面的命令替代上方上传步骤。<code>--rollout</code> 可重复指定已有 trial 或 job 目录，不会触发运行。</p><CodeExample title="任务与已有 Rollout 一起上传" code={pythonWithRollout} notify={notify} /></details> : <p className="api-guide-note">ZIP 内包含可识别的 trial 时会一并导入；也可上传任务后追加结果 ZIP。每次新上传使用新标识，重试保持 <code>HARBOR_UPLOAD_KEY</code> 不变。</p>}
        </div>
      </section>

      <details className="api-guide-disclosure">
        <summary><span>3. 追加产物与查询质检</span><ChevronDown size={17} /></summary>
        <div className="api-guide-disclosure-body"><p>沿用第 2 步的环境变量，将已有产物追加到该任务；仅任务作者或管理员可追加。只查询时运行下面最后一条命令。</p><CodeExample title={method === 'python' ? '追加已有结果并查询' : '追加结果 ZIP 并查询'} code={method === 'python' ? pythonFollowup : curlFollowup} notify={notify} /><dl className="api-guide-status"><div><dt><code>task.status</code></dt><dd><code>pending</code> 待评审 · <code>approved</code> 通过 · <code>changes_requested</code> 需修改</dd></div><div><dt><code>reviews</code></dt><dd>评审者的意见与结论。</dd></div><div><dt><code>rollouts[].reward</code></dt><dd>已有运行的 Reward，与人工质检状态分别保存。</dd></div></dl></div>
      </details>

      <details className="api-guide-disclosure">
        <summary><span>重试与上传限制</span><ChevronDown size={17} /></summary>
        <div className="api-guide-disclosure-body"><ul><li>Python 客户端根据内容和任务集生成幂等标识，重试会复用原导入。明确需要另一份导入时使用 <code>--new-upload</code>。</li><li>curl 重试保留原 <code>HARBOR_UPLOAD_KEY</code> 或 <code>HARBOR_ROLLOUT_KEY</code>。开始一次新的上传前，分别执行 <code>unset HARBOR_UPLOAD_KEY</code> 或 <code>unset HARBOR_ROLLOUT_KEY</code>。</li><li><code>Idempotency-Key</code> 最长 128 字符，限定同一 Token 和上传路径；同标识、同内容返回 <code>replayed: true</code>，改变内容或任务集返回 <code>409</code>。</li><li>创建任务集没有上传接口的幂等保证。断连或网关错误后，先列出任务集确认是否已创建。</li><li>单次上传最多 50 MB，展开后 100 MB，单文件 20 MB。ZIP 展开最多 2,000 个文件，直接 multipart 最多 1,000 个文件；大目录建议使用 ZIP 或自动打包的 Python CLI。符号链接与越界路径会被拒绝。</li></ul></div>
      </details>

      <details className="api-guide-disclosure">
        <summary><span>API 参考与常见错误</span><ChevronDown size={17} /></summary>
        <div className="api-guide-disclosure-body"><p>以下接口均需 <code>Authorization: Bearer $HARBOR_API_TOKEN</code>。省略 <code>project_id</code> 时使用 Token 项目；显式指定其他项目会被拒绝。</p><div className="api-guide-table-wrap"><table><thead><tr><th>方法</th><th>路径</th><th>用途</th></tr></thead><tbody>{endpoints.map(([methodName, path, usage]) => <tr key={`${methodName}${path}`}><td>{methodName}</td><td><code>{path}</code></td><td>{usage}</td></tr>)}</tbody></table></div><ul><li>新建任务集使用 JSON：<code>name</code> 为 1–80 字符，<code>project_id</code> 可选。响应为 <code>{'{task_set: {...}}'}</code>；列表返回 <code>task_sets</code>。</li><li>上传使用 multipart：必填 <code>files</code>，可填 <code>project_id</code>、<code>task_set_id</code>。任务支持 <code>description</code>；结果支持 <code>name</code>、<code>agent</code>、<code>model</code>。</li><li>直接上传多个文件时，重复 <code>files</code>，并用 <code>paths</code> 传对应顺序的 JSON 相对路径数组。</li><li>任务列表与详情使用查询参数 <code>task_set_id</code>。省略时，上传进入默认任务集，详情与追加识别任务实际归属。</li></ul><div className="api-guide-table-wrap"><table><thead><tr><th>状态码</th><th>处理方式</th></tr></thead><tbody><tr><td>400 / 422</td><td>检查文件、字段或任务结构。</td></tr><tr><td>401</td><td>Token 无效、已撤销或过期，重新获取。</td></tr><tr><td>403 / 404</td><td>检查项目、任务集归属及追加权限。</td></tr><tr><td>409</td><td>同一幂等标识对应了不同内容。</td></tr><tr><td>413</td><td>检查上传大小或文件数量。</td></tr><tr><td>5xx / 断连</td><td>上传保留原标识重试；新建任务集先检查列表。</td></tr></tbody></table></div><p className="api-guide-note">Token 元数据：用登录 Cookie 调用 <code>GET /api/auth/tokens</code>；撤销使用 <code>DELETE /api/auth/tokens/{'{token_id}'}</code>。需重新登录取得 Cookie，Bearer Token 不能调用这两个接口。</p></div>
      </details>
    </div>
  </div>;
}
