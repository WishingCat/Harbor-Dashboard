import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react';
import { ArrowDown, ArrowDownUp, ArrowLeft, ArrowUpRight, BookOpen, ChevronDown, ChevronRight, CircleCheck, CircleDashed, ExternalLink, FolderClosed, HelpCircle, LayoutGrid, ListFilter, LogIn, LogOut, Menu, Moon, Play, Plus, Search, Settings2, SquareTerminal, Sun, Tag as TagIcon, Users, X } from 'lucide-react';
import { api, post, projectPath, relativeTime } from './api';
import { orderTags } from './tags';
import { Avatar, Brand, categories, difficultyLabels, Empty, Loading, Modal, Status, statusLabels, WorkspaceSkeleton } from './components';
import { AuthModal, UploadModal } from './forms';
import TaskDetail from './TaskDetail';
import SettingsPage from './SettingsPage';
import { NavigationDrawer } from './NavigationDrawer';
import TaskSetsPage from './TaskSetsPage';
import { HierarchyCreateModal } from './HierarchyCreateModal';
import ProjectSwitcher from './ProjectSwitcher';
import ApiGuidePage from './ApiGuidePage';
import { useTheme } from './theme';
import type { Detail, Project, Rollout, Task, TaskSet, TaskStatus, User } from './types';

type Page = 'tasks'|'rollouts'|'api'|'settings';
const pageNames: Record<Page, string> = {tasks: '任务集', rollouts: 'Rollout 结果', api: 'API 接口', settings: '工作区设置'};
function currentPage(): Page {
  const value = new URLSearchParams(location.search).get('view');
  return value && Object.prototype.hasOwnProperty.call(pageNames, value) ? value as Page : 'tasks';
}

export default function App() {
  const [page, setPage] = useState<Page>(currentPage);
  const [projectId, setProjectId] = useState(new URLSearchParams(location.search).get('project') || 'project-aa');
  const [projects, setProjects] = useState<Project[]>([]);
  const [taskSets, setTaskSets] = useState<TaskSet[]>([]);
  const [taskSetId, setTaskSetId] = useState<string|null>(new URLSearchParams(location.search).get('task_set'));
  const [projectResolved, setProjectResolved] = useState(() => {
    const params = new URLSearchParams(location.search);
    return !params.get('task') || !!params.get('project');
  });
  const [taskId, setTaskId] = useState<string|null>(new URLSearchParams(location.search).get('task'));
  const [requestedRollout, setRequestedRollout] = useState<string|undefined>(new URLSearchParams(location.search).get('rollout') || undefined);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [user, setUser] = useState<User|null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [query, setQuery] = useState('');
  const [authOpen, setAuthOpen] = useState(false);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [createKind, setCreateKind] = useState<'project'|'task-set'|null>(null);
  const {theme, toggleTheme} = useTheme();
  const [mobileNav, setMobileNav] = useState(false);
  const [desktopNavigation, setDesktopNavigation] = useState(() => matchMedia('(min-width: 1101px)').matches);
  const [toast, setToast] = useState('');
  const searchRef = useRef<HTMLInputElement>(null);
  const pendingNavigationFocus = useRef<{navigation: HTMLElement | null; focused: HTMLElement} | null>(null);
  const activeProject = useRef(projectId);
  activeProject.current = projectId;
  const requestVersion = useRef(0);
  const authVersion = useRef(0);
  const uploadVersion = useRef(0);
  const createVersion = useRef(0);
  const currentUploadVersion = uploadVersion.current;
  const currentCreateVersion = createVersion.current;
  const project = projects.find(item => item.id === projectId);
  const taskSet = taskSets.find(item => item.id === taskSetId);
  const navigationDocked = desktopNavigation && !taskId;
  useEffect(() => {
    const media = matchMedia('(min-width: 1101px)');
    const hasOtherDialog = () => !!document.querySelector('[role="dialog"], dialog[open]:not(#workspace-navigation)');
    const update = () => {
      const navigation = document.getElementById('workspace-navigation');
      const focused = document.activeElement as HTMLElement | null;
      const restoreNavigationFocus = !!focused && (navigation?.contains(focused) || focused.matches('.navigation-toggle')) && !hasOtherDialog();
      pendingNavigationFocus.current = restoreNavigationFocus && focused ? {navigation, focused} : null;
      setDesktopNavigation(media.matches);
    };
    media.addEventListener('change', update);
    return () => {media.removeEventListener('change', update); pendingNavigationFocus.current = null;};
  }, []);
  useLayoutEffect(() => {
    const pending = pendingNavigationFocus.current;
    pendingNavigationFocus.current = null;
    if (!pending || pending.navigation === document.getElementById('workspace-navigation')) return;
    if (document.querySelector('[role="dialog"], dialog[open]:not(#workspace-navigation)')) return;
    if (document.activeElement !== document.body && document.activeElement !== pending.focused) return;
    const pinned = document.querySelector('.navigation-persistent');
    const target = pinned
      ? pinned.querySelector<HTMLElement>('.nav-link.active') || pinned.querySelector<HTMLElement>('.brand-link')
      : document.querySelector<HTMLElement>('.navigation-toggle');
    target?.focus({preventScroll: true});
  }, [desktopNavigation, navigationDocked]);
  useEffect(() => {if (navigationDocked) setMobileNav(false);}, [navigationDocked]);
  const notify = useCallback((message: string) => setToast(message), []);
  const refresh = useCallback(async () => {
    if (activeProject.current !== projectId) return;
    const version = ++requestVersion.current;
    const authAtStart = authVersion.current;
    const current = () => version === requestVersion.current && activeProject.current === projectId;
    setError('');
    try {
      const projectData = await api<{projects: Project[]}>('/projects');
      if (!current()) return;
      setProjects(projectData.projects);
      if (!projectData.projects.some(item => item.id === projectId)) throw new Error('项目不存在，请切换项目。');
      const [taskData, authData, setData] = await Promise.all([
        api<{tasks: Task[]}>(projectPath('/tasks', projectId)),
        api<{user: User|null}>('/auth/me'),
        api<{task_sets: TaskSet[]}>(projectPath('/task-sets', projectId)),
      ]);
      if (!current()) return;
      setTasks(taskData.tasks); setTaskSets(setData.task_sets);
      if (authVersion.current === authAtStart) setUser(authData.user);
    } catch (e) {if (current()) setError((e as Error).message);}
    finally {if (current()) setBusy(false);}
  }, [projectId]);
  useEffect(() => {
    if (projectResolved) return;
    let alive = true;
    const id = new URLSearchParams(location.search).get('task');
    // Existing task-only links resolve their project before loading scoped lists.
    api<Detail>(`/tasks/${encodeURIComponent(id || '')}`).then(detail => {
      if (!alive) return;
      if (activeProject.current !== detail.task.project_id) {
        requestVersion.current++; uploadVersion.current++;
        setBusy(true); setTasks([]); setQuery(''); setUploadOpen(false);
      }
      activeProject.current = detail.task.project_id;
      setProjectId(detail.task.project_id);
      setTaskSetId(detail.task.task_set_id);
      const url = new URL(location.href);
      url.searchParams.set('project', detail.task.project_id);
      url.searchParams.set('task_set', detail.task.task_set_id);
      history.replaceState(null, '', url);
    }).catch(() => {}).finally(() => {if (alive) setProjectResolved(true);});
    return () => {alive = false;};
  }, [projectResolved, taskId]);
  useEffect(() => {
    // Existing project/task links acquire their collection without changing the task URL contract.
    const task = tasks.find(item => item.id === taskId);
    if (!task || taskSetId || !projectResolved) return;
    setTaskSetId(task.task_set_id);
    const url = new URL(location.href);
    url.searchParams.set('task_set', task.task_set_id);
    history.replaceState(null, '', url);
  }, [tasks, taskId, taskSetId, projectResolved]);
  useEffect(() => {if (projectResolved) void refresh(); return () => {requestVersion.current++;};}, [refresh, projectResolved]);
  useLayoutEffect(() => {window.scrollTo(0, 0);}, [page, projectId, taskSetId, taskId, requestedRollout]);
  useEffect(() => {if (!toast) return; const timeout = setTimeout(() => setToast(''), 4500); return () => clearTimeout(timeout);}, [toast]);
  const openTask = useCallback((id: string|null, rollout?: string) => {
    setTaskId(id); setRequestedRollout(rollout); setMobileNav(false);
    const collection = id ? tasks.find(task => task.id === id)?.task_set_id || taskSetId : taskSetId;
    setTaskSetId(collection);
    const url = new URL(location.href);
    url.searchParams.set('project', projectId);
    if (collection) url.searchParams.set('task_set', collection); else url.searchParams.delete('task_set');
    if (id) url.searchParams.set('task', id); else url.searchParams.delete('task');
    if (rollout) url.searchParams.set('rollout', rollout); else url.searchParams.delete('rollout');
    history.pushState(null, '', url);
  }, [projectId, taskSetId, tasks]);
  const openTaskSet = useCallback((id: string|null) => {
    setPage('tasks'); setTaskSetId(id); setTaskId(null); setRequestedRollout(undefined);
    setQuery(''); setMobileNav(false); uploadVersion.current++; setUploadOpen(false);
    const url = new URL(location.href);
    url.searchParams.set('project', projectId);
    if (id) url.searchParams.set('task_set', id); else url.searchParams.delete('task_set');
    url.searchParams.delete('task'); url.searchParams.delete('rollout'); url.searchParams.delete('view');
    history.pushState(null, '', url);
  }, [projectId]);
  const changeProject = (next: string) => {
    if (next === projectId) return;
    const nextPage = page === 'api' ? 'api' : 'tasks';
    requestVersion.current++; uploadVersion.current++; activeProject.current = next;
    setBusy(true); setError(''); setTasks([]); setTaskSets([]); setTaskSetId(null);
    setProjectId(next); setProjectResolved(true); setTaskId(null); setRequestedRollout(undefined);
    setPage(nextPage); setQuery(''); setUploadOpen(false); createVersion.current++; setCreateKind(null); setMobileNav(false);
    const url = new URL(location.href);
    url.searchParams.set('project', next); url.searchParams.delete('task'); url.searchParams.delete('rollout');
    url.searchParams.delete('task_set');
    if (nextPage === 'api') url.searchParams.set('view', 'api'); else url.searchParams.delete('view');
    history.pushState(null, '', url);
  };
  useEffect(() => {
    const shortcut = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k' && !document.querySelector('[role="dialog"], dialog[open]')) {
        if (!searchRef.current && !(taskId && taskSetId)) return;
        event.preventDefault();
        if (!searchRef.current) openTaskSet(taskSetId);
        requestAnimationFrame(() => searchRef.current?.focus());
      }
    };
    const pop = () => {
      const params = new URLSearchParams(location.search);
      const next = params.get('project') || 'project-aa';
      setMobileNav(false); createVersion.current++; setCreateKind(null); setQuery(''); setPage(currentPage());
      uploadVersion.current++; setUploadOpen(false);
      if (next !== activeProject.current || (params.get('task') && !params.get('project'))) {
        requestVersion.current++; activeProject.current = next;
        setBusy(true); setTasks([]); setQuery(''); setUploadOpen(false);
        setProjectId(next);
      }
      setTaskId(params.get('task')); setRequestedRollout(params.get('rollout') || undefined);
      setTaskSetId(params.get('task_set'));
      setProjectResolved(!params.get('task') || !!params.get('project'));
    };
    window.addEventListener('keydown', shortcut); window.addEventListener('popstate', pop);
    return () => {window.removeEventListener('keydown', shortcut); window.removeEventListener('popstate', pop);};
  }, [openTaskSet, taskId, taskSetId]);
  const navigate = (next: Page) => {
    createVersion.current++; setCreateKind(null);
    setPage(next); setTaskId(null); setTaskSetId(null); setRequestedRollout(undefined); setQuery(''); setMobileNav(false);
    uploadVersion.current++; setUploadOpen(false);
    const url = new URL(location.href);
    url.searchParams.set('project', projectId);
    url.searchParams.delete('task'); url.searchParams.delete('task_set'); url.searchParams.delete('rollout');
    if (next === 'tasks') url.searchParams.delete('view'); else url.searchParams.set('view', next);
    history.pushState(null, '', url);
  };
  const requestCreate = (kind: 'project'|'task-set') => {setMobileNav(false); createVersion.current++; if (user) setCreateKind(kind); else setAuthOpen(true);};
  const requestUpload = () => {if (!taskSet) return; if (user) {uploadVersion.current++; setUploadOpen(true);} else setAuthOpen(true);};
  const scopedTasks = taskSetId ? tasks.filter(task => task.task_set_id === taskSetId) : tasks;
  const matchedTasks = scopedTasks.filter(t => `${t.title} ${t.slug} ${t.summary} ${t.description} ${t.tags.join(' ')} ${t.author}`.toLowerCase().includes(query.toLowerCase()));
  const nav = (id: Page, Icon: typeof LayoutGrid) => <button key={id} className={`nav-link ${page === id ? 'active' : ''}`} onClick={() => navigate(id)}><Icon size={18} strokeWidth={1.7} /><span>{pageNames[id]}</span></button>;
  return <div className={`app-shell ${taskId ? 'reading-view' : ''} ${navigationDocked ? 'navigation-pinned' : ''}`}>
    <NavigationDrawer open={mobileNav} docked={navigationDocked} onClose={() => setMobileNav(false)}>
    <aside className="sidebar">
      <div className="navigation-heading"><button className="brand-link" onClick={() => navigate('tasks')} aria-label="Harbor Dashboard 首页"><Brand /></button>{!navigationDocked && <button className="icon-button" aria-label="关闭导航" onClick={() => setMobileNav(false)}><X size={19} /></button>}</div>
      <ProjectSwitcher projects={projects} projectId={projectId} onChange={changeProject} onCreate={() => requestCreate('project')} active={navigationDocked || mobileNav} />
      <nav aria-label="主要导航">{nav('tasks', LayoutGrid)}{tasks.some(task => task.rollouts_count > 0) && nav('rollouts', Play)}</nav>
      <div className="sidebar-bottom">
        <nav aria-label="工作区工具">{nav('api', SquareTerminal)}{nav('settings', Settings2)}<button className="nav-link" onClick={() => {setMobileNav(false); setHelpOpen(true);}}><BookOpen size={18} strokeWidth={1.7} /><span>使用指南</span><ArrowUpRight size={13} className="nav-external" /></button></nav>
        <div className="sidebar-user">{user ? <><Avatar name={user.name} /><div className="user-info"><strong>{user.name}</strong></div><button className="icon-button" aria-label="退出登录" onClick={async () => {try {await post('/auth/logout', {}); authVersion.current++; setUser(null); setMobileNav(false); notify('已退出登录');} catch (e) {notify((e as Error).message);}}}><LogOut size={16} /></button></> : <><span className="guest-avatar"><Users size={18} /></span><div className="user-info"><strong>访客</strong></div><button className="icon-button" aria-label="登录" onClick={() => {setMobileNav(false); setAuthOpen(true);}}><LogIn size={17} /></button></>}</div>
      </div>
    </aside>
    </NavigationDrawer>
    <div className="workspace-main"><header className="topbar"><div className="breadcrumb">
      {!navigationDocked && <button className="icon-button navigation-toggle" aria-label="打开导航" title="工作区导航" aria-expanded={mobileNav} aria-controls="workspace-navigation" onClick={() => setMobileNav(true)}><Menu size={20} /></button>}
      <button className="breadcrumb-project breadcrumb-link" onClick={() => navigate('tasks')} title={project?.name}>{project?.name || '项目'}</button><ChevronRight size={13} />
      {taskSet && <><button className="breadcrumb-link breadcrumb-task-set" title={taskSet.name} onClick={() => openTaskSet(taskSet.id)}>{taskSet.name}</button><ChevronRight size={13} /></>}
      <strong>{taskId ? '任务详情' : taskSet ? '任务' : page === 'tasks' ? '任务集' : pageNames[page]}</strong>
    </div><div className="top-actions"><button className="icon-button theme-toggle" aria-label={theme === 'dark' ? '切换浅色模式' : '切换暗黑模式'} title={theme === 'dark' ? '切换浅色模式' : '切换暗黑模式'} onClick={toggleTheme}>{theme === 'dark' ? <Sun size={19} /> : <Moon size={19} />}</button><button className="icon-button" aria-label="使用帮助" onClick={() => setHelpOpen(true)}><HelpCircle size={19} /></button>{user ? <Avatar name={user.name} size="small" /> : <button className="top-login" onClick={() => setAuthOpen(true)}>登录 <ArrowUpRight size={13} /></button>}</div></header>
      <main>{error ? <div className="page-content"><Empty title="暂时无法连接工作区" description={error} action={<button className="button primary" onClick={() => {setBusy(true); void refresh();}}>重新连接</button>} /></div>
        : busy || !project || !projectResolved ? <WorkspaceSkeleton />
        : taskSetId && (!taskSet || (taskId && tasks.some(task => task.id === taskId && task.task_set_id !== taskSetId))) ? <div className="page-content"><Empty title="任务集不存在或不属于当前项目" description="" action={<button className="button secondary" onClick={() => openTaskSet(null)}>返回任务集</button>} /></div>
        : taskId ? <TaskDetail key={`${project.id}-${taskId}-${requestedRollout || 'task'}`} project={project} taskSetId={taskSetId || undefined} id={taskId} backLabel="任务列表" initialRolloutId={requestedRollout} user={user} onBack={() => openTaskSet(taskSetId)} onLogin={() => setAuthOpen(true)} onRefresh={refresh} notify={notify} />
        : page === 'api' ? <ApiGuidePage key={project.id} project={project} projects={projects} taskSets={taskSets} user={user} onProjectChange={changeProject} onLogin={() => setAuthOpen(true)} notify={notify} />
        : page === 'settings' ? <SettingsPage user={user} onLogin={() => setAuthOpen(true)} notify={notify} />
        : page === 'rollouts' ? <RolloutsPage key={project.id} tasks={scopedTasks} onOpen={openTask} />
        : page === 'tasks' && !taskSet ? <TaskSetsPage project={project} taskSets={taskSets} onOpen={openTaskSet} onCreate={() => requestCreate('task-set')} onDeleted={refresh} notify={notify} />
        : <TaskLibrary key={`${project.id}-${taskSetId || 'all'}-${page}`} title={taskSet?.name} onBack={taskSet ? () => openTaskSet(null) : undefined} tasks={matchedTasks} allTasks={scopedTasks} query={query} onQuery={setQuery} onOpen={openTask} onUpload={taskSet ? requestUpload : undefined} searchRef={searchRef} />}</main>

    </div>
    {authOpen && <AuthModal onClose={() => setAuthOpen(false)} onSuccess={account => {authVersion.current++; setUser(account); setAuthOpen(false); notify(`欢迎，${account.name}`);}} />}
    {uploadOpen && project && taskSet && <UploadModal key={taskSet.id} project={project} taskSet={taskSet} onClose={() => {uploadVersion.current++; setUploadOpen(false);}} onSuccess={id => {if (activeProject.current !== project.id || uploadVersion.current !== currentUploadVersion) return; setUploadOpen(false); void refresh(); openTask(id); notify('任务已上传');}} />}
    {createKind && <HierarchyCreateModal key={`${createKind}-${projectId}`} kind={createKind} project={project} onClose={() => {createVersion.current++; setCreateKind(null);}} onSuccess={item => {
      if (createVersion.current !== currentCreateVersion) return;
      createVersion.current++; setCreateKind(null);
      if (createKind === 'project') {setProjects(previous => [...previous, item as Project]); changeProject(item.id); notify('项目已创建');}
      else if ('project_id' in item && item.project_id === activeProject.current) {setTaskSets(previous => [...previous, item]); openTaskSet(item.id); void refresh(); notify('任务集已创建');}
    }} />}
    {helpOpen && <Modal title="使用指南" onClose={() => setHelpOpen(false)}><div className="guide-content"><div><span>01</span><section><h3>上传任务</h3><p>先新建或选择项目和任务集，再上传 ZIP 或文件夹。平台不限制包内格式，上传什么就按文件展示什么；是标准 Harbor 任务时会额外读取 task.toml 的信息。任务自动命名，简介与标签可选。</p></section></div><div><span>02</span><section><h3>查看已有结果</h3><p>上传包中包含 Rollout 结果时，可查看 result.json、Agent 轨迹和产物文件。</p></section></div><div><span>03</span><section><h3>阅读、翻译与评审</h3><p>选择文件阅读，开启双语翻译。登录后可提出修改建议，并提交“通过”或“需修改”的评审。</p></section></div><a className="button secondary" href="https://www.harborframework.com/docs" target="_blank" rel="noreferrer">Harbor 官方文档<ExternalLink size={14} /></a><p className="form-footnote">带“临时”标记的任务用于试用。首次注册的账号为管理员。翻译由平台统一提供，登录后即可使用。</p></div></Modal>}
    {toast && <div className="toast" role="status"><CircleCheck size={17} />{toast}<button aria-label="关闭提示" onClick={() => setToast('')}><X size={14} /></button></div>}
  </div>;
}

function TaskLibrary({title, onBack, tasks, allTasks, query, onQuery, onOpen, onUpload, searchRef}: {title?: string; onBack?: () => void; tasks: Task[]; allTasks: Task[]; query: string; onQuery: (v: string) => void; onOpen: (id: string) => void; onUpload?: () => void; searchRef: RefObject<HTMLInputElement|null>}) {
  const [status, setStatus] = useState<TaskStatus|'all'>('all');
  const [category, setCategory] = useState('all');
  const [oldest, setOldest] = useState(false);
  const [filterOpen, setFilterOpen] = useState(false);
  const [difficulty, setDifficulty] = useState('all');
  const [tag, setTag] = useState('all');
  const [showDemos, setShowDemos] = useState(true);
  // Offer every tag present in this collection, not only the preset vocabulary.
  const availableTags = useMemo(() => orderTags(allTasks.flatMap(t => t.tags)), [allTasks]);
  const filtered = useMemo(() => tasks.filter(t => (status === 'all' || t.status === status) && (category === 'all' || t.category === category) && (difficulty === 'all' || t.difficulty === difficulty) && (tag === 'all' || t.tags.includes(tag)) && (showDemos || !t.is_demo)).sort((a, b) => (new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()) * (oldest ? -1 : 1)), [tasks, status, category, oldest, difficulty, tag, showDemos]);
  const totalRollouts = allTasks.reduce((sum, t) => sum + t.rollouts_count, 0);
  const approved = allTasks.filter(t => t.status === 'approved').length;
  const pending = allTasks.filter(t => t.status === 'pending').length;
  return <div className="page-content library-page">
    {onBack && <button className="back-link collection-back" onClick={onBack}><ArrowLeft size={15} />任务集</button>}
    <div className="page-heading"><h1>{title || '任务'}</h1>{onUpload && <div className="heading-actions"><button className="button primary" onClick={onUpload}><Plus size={18} />上传任务</button></div>}</div>
    <div className="metrics-strip"><Metric label="任务" value={allTasks.length} />{totalRollouts > 0 && <Metric label="Rollout 结果" value={totalRollouts} />}<Metric label="待评审" value={pending} /><Metric label="已通过" value={approved} /></div>
    <div className="library-layout"><section className="task-section"><div className="section-tabs" role="tablist" aria-label="按评审状态筛选">{(['all', 'pending', 'approved', 'changes_requested'] as const).map(s => <button key={s} role="tab" aria-selected={status === s} className={status === s ? 'selected' : ''} onClick={() => setStatus(s)}>{s === 'all' ? '全部任务' : statusLabels[s]}</button>)}</div>
      <div className="table-toolbar"><div className="table-search"><Search size={16} /><input ref={searchRef} aria-label="筛选任务列表" value={query} onChange={e => onQuery(e.target.value)} placeholder="搜索任务、标签…" />{query && <button className="icon-button" aria-label="清除搜索" onClick={() => onQuery('')}><X size={13} /></button>}</div><div className="filter-actions"><label className="category-select"><FolderClosed size={14} /><select aria-label="任务分类" value={category} onChange={e => setCategory(e.target.value)}><option value="all">所有分类</option>{Object.entries(categories).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select><ChevronDown size={12} /></label>{availableTags.length > 0 && <label className="category-select tag-select"><TagIcon size={14} /><select aria-label="任务标签" value={tag} onChange={e => setTag(e.target.value)}><option value="all">所有标签</option>{availableTags.map(value => <option value={value} key={value}>{value}</option>)}</select><ChevronDown size={12} /></label>}<button title={oldest ? '当前最早更新在前，切换为最近更新' : '当前最近更新在前，切换为最早更新'} aria-label="切换排序" className={`toolbar-icon ${oldest ? 'active' : ''}`} onClick={() => setOldest(!oldest)}><ArrowDownUp size={16} /></button><button className={`toolbar-icon ${filterOpen ? 'active' : ''}`} aria-label="更多筛选" aria-expanded={filterOpen} onClick={() => setFilterOpen(!filterOpen)}><ListFilter size={17} /></button></div></div>
      {filterOpen && <div className="expanded-filters"><label>难度<select value={difficulty} onChange={e => setDifficulty(e.target.value)}><option value="all">所有难度</option>{Object.entries(difficultyLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label><label className="checkbox-label"><input type="checkbox" checked={showDemos} onChange={e => setShowDemos(e.target.checked)} />显示临时任务</label><button className="text-button" onClick={() => {setDifficulty('all'); setShowDemos(true); setCategory('all'); setTag('all'); onQuery(''); setStatus('all');}}>重置筛选</button></div>}
      <div className="task-table-wrap"><table className="task-table"><thead><tr><th>任务名称 <ArrowDown size={11} /></th><th>评审状态</th>{totalRollouts > 0 && <th>Rollout 结果</th>}<th>最近更新</th><th aria-label="查看任务" /></tr></thead><tbody>{filtered.map(task => <tr key={task.id} onClick={() => onOpen(task.id)}><td><div className="task-cell"><div><button className="task-title" title={task.title} onClick={e => {e.stopPropagation(); onOpen(task.id);}}>{task.title}</button>{task.summary && <div className="task-summary" title={task.summary}>{task.summary}</div>}<div className="task-subline"><span className="task-slug" title={task.slug}>{task.slug}</span><span className="tiny-dot" /><span>{categories[task.category] || task.category}</span><span className="tiny-dot" /><span className={`difficulty ${task.difficulty}`}>{difficultyLabels[task.difficulty]}</span>{task.tags.slice(0, 3).map(value => <span className={`tag ${value === tag ? 'tag-active' : ''}`} key={value}>{value}</span>)}{task.tags.length > 3 && <span className="tag tag-more">+{task.tags.length - 3}</span>}{task.is_demo && <span className="demo-label">临时</span>}</div></div></div></td><td><Status status={task.status} /></td>{totalRollouts > 0 && <td>{task.rollouts_count > 0 && <span className="rollout-count"><Play size={12} />{task.rollouts_count}</span>}</td>}<td><span className="date-cell">{relativeTime(task.updated_at)}</span><span className="author-cell">{task.author}</span></td><td><ChevronRight size={15} className="row-chevron" /></td></tr>)}</tbody></table>{!filtered.length && <Empty icon={<Search size={26} />} title={allTasks.length ? '没有找到任务' : '任务集中还没有任务'} description={allTasks.length ? '调整搜索或筛选条件。' : ''} />}</div><div className="table-footer"><span>{filtered.length === allTasks.length ? `共 ${allTasks.length} 个任务` : `${filtered.length} / ${allTasks.length} 个任务`}</span></div>
    </section></div>
  </div>;
}

function Metric({label, value}: {label: string; value: number}) {return <div className="metric"><span className="metric-label">{label}</span><strong>{value}</strong></div>;}

function RolloutsPage({tasks, onOpen}: {tasks: Task[]; onOpen: (id: string, rollout?: string) => void}) {
  const [rows, setRows] = useState<{task: Task; rollout: Rollout}[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(true);
  const [filter, setFilter] = useState('all');
  useEffect(() => {let alive = true; Promise.all(tasks.filter(t => t.rollouts_count).map(t => api<Detail>(projectPath(`/tasks/${t.id}`, t.project_id)))).then(details => {if (alive) setRows(details.flatMap(d => d.rollouts.map(rollout => ({task: d.task, rollout}))));}).catch(e => {if (alive) setError(e.message);}).finally(() => {if (alive) setBusy(false);}); return () => {alive = false;};}, [tasks]);
  return <div className="page-content"><div className="page-heading"><h1>Rollout 结果<span className="heading-count">{rows.length}</span></h1></div><div className="section-tabs">{[['all', '全部结果'], ['passed', '成功'], ['failed', '失败'], ['unknown', '未判定']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)}>{label}</button>)}</div>{busy ? <Loading /> : error ? <Empty title="运行记录加载失败" description={error} /> : <div className="task-table-wrap"><table className="task-table rollouts-table"><thead><tr><th>运行 / 所属任务</th><th>Agent / 模型</th><th>运行结果</th><th>Reward</th><th>文件</th><th>创建时间</th></tr></thead><tbody>{rows.filter(({rollout}) => filter === 'all' || rollout.status === filter).map(({task, rollout}) => <tr key={rollout.id} onClick={() => onOpen(task.id, rollout.id)}><td><button className="task-title" title={rollout.name} onClick={event => {event.stopPropagation(); onOpen(task.id, rollout.id);}}>{rollout.name}</button><span className="author-cell">{task.title}</span></td><td><span className="mono small-text">{rollout.agent || 'N/A'}</span><span className="author-cell">{rollout.model || '未指定模型'}</span></td><td><span className={`status ${rollout.status === 'passed' ? 'approved' : rollout.status === 'failed' ? 'changes_requested' : 'pending'}`}>{rollout.status === 'passed' ? <CircleCheck size={13} /> : <CircleDashed size={13} />}{rollout.status === 'passed' ? '成功' : rollout.status === 'failed' ? '失败' : '未判定'}</span></td><td className="mono">{rollout.reward ?? 'N/A'}</td><td>{rollout.files.length}</td><td className="date-cell">{relativeTime(rollout.created_at)}</td></tr>)}</tbody></table>{!rows.filter(({rollout}) => filter === 'all' || rollout.status === filter).length && <Empty icon={<Play size={25} />} title="暂无 Rollout 结果" description="" />}</div>}</div>;
}
