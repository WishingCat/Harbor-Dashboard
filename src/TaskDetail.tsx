import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties, FormEvent, MouseEvent as ReactMouseEvent } from 'react';
import { ArrowLeft, ArrowUpRight, Check, ChevronDown, ChevronRight, CircleCheck, Clock3, Code2, Download, Eye, FileText, FolderClosed, FolderOpen, GitBranch, Languages, Link2, LoaderCircle, MessageSquare, Play, RefreshCw, Send, Trash2, X } from 'lucide-react';
import { Download as DownloadIcon, FolderDown } from 'lucide-react';
import { api, post, projectPath, relativeTime, sizeLabel } from './api';
import { Avatar, categories, difficultyLabels, Empty, FileIcon, Loading, Status } from './components';
import ArtifactMarkdown from './ArtifactMarkdown';
import { lockPageScroll } from './pageScrollLock';
import type { Artifact, Detail, Project, Review, Task, User } from './types';
import './detail.css';

type Props = {id: string; project: Project; taskSetId?: string; backLabel?: string; initialRolloutId?: string; user: User|null; onBack: () => void; onLogin: () => void; onRefresh: () => Promise<void>; notify: (text: string) => void};

const DRAWER_QUERY = '(max-width: 1100px)';
type AuxiliaryPanel = 'files'|'reviews';

export default function TaskDetail({id, project, taskSetId, backLabel = '任务集', initialRolloutId, user, onBack, onLogin, onRefresh, notify}: Props) {
  const [detail, setDetail] = useState<Detail|null>(null);
  const [error, setError] = useState('');
  const [selected, setSelected] = useState<Artifact|null>(null);
  const [source, setSource] = useState<'task'|'rollout'>('task');
  const detailLoaded = useRef(false);
  const [rolloutId, setRolloutId] = useState(initialRolloutId || '');
  const [translationOpen, setTranslationOpen] = useState(false);
  const [activePanel, setActivePanel] = useState<AuxiliaryPanel|null>(null);
  const [infoOpen, setInfoOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [menu, setMenu] = useState<{at: {x: number; y: number}; target: TreeTarget} | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [isDrawer, setIsDrawer] = useState(() => matchMedia(DRAWER_QUERY).matches);
  const filesButton = useRef<HTMLButtonElement>(null);
  const reviewsButton = useRef<HTMLButtonElement>(null);
  const translationButton = useRef<HTMLButtonElement>(null);
  const filesPanel = useRef<HTMLElement>(null);
  const reviewsPanel = useRef<HTMLElement>(null);
  const documentHost = useRef<HTMLDivElement>(null);
  const load = useCallback(async () => {
    try {const result = await api<Detail>(projectPath(`/tasks/${id}`, project.id) + (taskSetId ? `&task_set_id=${encodeURIComponent(taskSetId)}` : '')); setDetail(result);
      const run = result.rollouts.find(r => r.id === initialRolloutId);
      const choices = run ? run.files : result.files;
      const preferred = choices.find(f => f.path.endsWith(run ? 'result.json' : 'instruction.md')) || choices[0] || null;
      setSelected(prev => prev || preferred);
      setRolloutId(prev => result.rollouts.some(item => item.id === prev) ? prev : run?.id || result.rollouts[0]?.id || '');
      if (!detailLoaded.current) {setSource(run ? 'rollout' : 'task'); detailLoaded.current = true;}
      if (!result.rollouts.length) setSource('task');
      setError('');}
    catch (e) {setActivePanel(null); setError((e as Error).message);}
  }, [id, initialRolloutId, project.id, taskSetId]);
  useEffect(() => {void load();}, [load]);
  // Deleting cannot be undone, so the button arms first and disarms on its own.
  useEffect(() => {
    if (!confirmDelete) return;
    const timer = setTimeout(() => setConfirmDelete(false), 5000);
    return () => clearTimeout(timer);
  }, [confirmDelete]);
  useEffect(() => {
    const media = matchMedia(DRAWER_QUERY);
    const update = () => {
      const directoryHadFocus = filesPanel.current?.contains(document.activeElement) || filesButton.current === document.activeElement;
      setIsDrawer(media.matches);
      // The desktop directory is persistent; drawer state only belongs to narrow screens.
      setActivePanel(current => current === 'files' ? null : current);
      if (directoryHadFocus) requestAnimationFrame(() => {
        if (media.matches) filesButton.current?.focus({preventScroll: true});
        else (filesPanel.current?.querySelector<HTMLElement>('.tree-file.selected') || filesPanel.current)?.focus({preventScroll: true});
      });
    };
    media.addEventListener('change', update);
    return () => media.removeEventListener('change', update);
  }, []);
  const closePanel = useCallback((restoreFocus = true) => {
    const trigger = activePanel === 'files' ? filesButton.current : reviewsButton.current;
    setActivePanel(null);
    if (restoreFocus) requestAnimationFrame(() => trigger?.focus({preventScroll: true}));
  }, [activePanel]);
  useEffect(() => {
    if (!activePanel) return;
    const panel = activePanel === 'files' ? filesPanel.current : reviewsPanel.current;
    if (!panel) return;
    const unlock = isDrawer ? lockPageScroll() : undefined;
    const frame = requestAnimationFrame(() => {
      if (!panel.contains(document.activeElement) && !document.querySelector('dialog[open], .modal-backdrop')) panel.querySelector<HTMLButtonElement>('.reader-panel-close')?.focus({preventScroll: true});
    });
    const onKey = (event: KeyboardEvent) => {
      // Native navigation and authentication dialogs own focus while on top.
      if ([...document.querySelectorAll<HTMLElement>('dialog[open], [role="dialog"]')].some(dialog => dialog !== panel && dialog.getClientRects().length > 0)) return;
      if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); closePanel(); return;}
      if (!isDrawer || event.key !== 'Tab') return;
      const controls = [...panel.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]')].filter(element => element.getClientRects().length > 0 && !element.closest('[hidden], [inert]'));
      const first = controls[0], last = controls[controls.length - 1];
      if (!first) {event.preventDefault(); panel.focus();}
      else if (event.shiftKey && (document.activeElement === first || !panel.contains(document.activeElement))) {event.preventDefault(); last.focus();}
      else if (!event.shiftKey && (document.activeElement === last || !panel.contains(document.activeElement))) {event.preventDefault(); first.focus();}
    };
    document.addEventListener('keydown', onKey);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener('keydown', onKey);
      unlock?.();
    };
  }, [activePanel, isDrawer, closePanel]);
  const requestLogin = () => {if (isDrawer) closePanel(false); onLogin();};
  function focusDocument() {
    requestAnimationFrame(() => {
      documentHost.current?.focus({preventScroll: true});
      documentHost.current?.scrollIntoView({block: 'start', behavior: 'auto'});
    });
  }
  function selectDocument(file: Artifact) {
    setSelected(file);
    if (activePanel === 'files') closePanel(false);
    focusDocument();
  }
  function togglePanel(panel: AuxiliaryPanel) {if (activePanel === panel) closePanel(); else setActivePanel(panel);}
  if (error) return <div className="page-content"><button className="back-link" onClick={onBack}><ArrowLeft size={15} />返回{backLabel}</button><Empty title="任务暂时无法打开" description={error} action={<button className="button secondary" onClick={() => void load()}>重试</button>} /></div>;
  if (!detail) return <Loading text="读取任务…" />;
  const {task, files, rollouts, reviews} = detail;
  const rollout = rollouts.find(r => r.id === rolloutId);
  const visibleFiles = source === 'task' ? files : rollout?.files || [];
  const filesVisible = !isDrawer || activePanel === 'files';
  function switchSource(next: 'task'|'rollout') {
    setSource(next);
    const fileList = next === 'task' ? files : rollout?.files || [];
    setSelected(fileList.find(f => f.path.endsWith(next === 'task' ? 'instruction.md' : 'result.json')) || fileList[0] || null);
  }
  async function removeTask() {
    if (deleting) return;
    if (!confirmDelete) {setConfirmDelete(true); return;}
    setConfirmDelete(false); setDeleting(true);
    try {
      await api(projectPath(`/tasks/${id}`, project.id), {method: 'DELETE'});
      notify('任务已删除');
      onBack();
      await onRefresh();
    } catch (e) {notify((e as Error).message); setDeleting(false);}
  }
  function openMenu(event: ReactMouseEvent, target: TreeTarget) {
    event.preventDefault(); event.stopPropagation();
    setMenu({at: {x: event.clientX, y: event.clientY}, target});
  }
  function downloadTarget(target: TreeTarget) {
    // A plain navigation keeps the session cookie and lets Content-Disposition
    // do the saving, so the page itself never leaves.
    if (target.kind === 'file') {
      location.href = projectPath(`/api/files/${encodeURIComponent(target.file.id)}/download`, project.id);
      return;
    }
    const params = new URLSearchParams({project_id: project.id, prefix: target.path});
    if (source === 'rollout' && rolloutId) params.set('rollout_id', rolloutId);
    location.href = `/api/tasks/${id}/archive?${params}`;
  }
  function downloadEverything() {
    const params = new URLSearchParams({project_id: project.id});
    if (source === 'rollout' && rolloutId) params.set('rollout_id', rolloutId);
    location.href = `/api/tasks/${id}/archive?${params}`;
  }
  async function shareTask() {
    try {
      const link = new URL(location.href);
      link.searchParams.set('project', project.id); link.searchParams.set('task', task.id);
      link.searchParams.set('task_set', task.task_set_id);
      if (source === 'rollout' && rollout) link.searchParams.set('rollout', rollout.id); else link.searchParams.delete('rollout');
      link.hash = ''; await navigator.clipboard.writeText(link.toString()); notify('任务链接已复制');
    } catch {notify('复制失败，请从浏览器地址栏复制链接');}
  }
  return <div className={`detail-page reader-focus ${rollouts.length ? 'has-rollouts' : 'task-only'}`}>
    {isDrawer && <button ref={filesButton} className={`reader-directory-trigger ${activePanel === 'files' ? 'is-active' : ''}`} aria-expanded={activePanel === 'files'} aria-controls="task-files-panel" onClick={() => togglePanel('files')}><FolderOpen size={18} /><span>目录</span></button>}
    <div className="detail-heading">
      <div className="reader-heading-top"><button className="back-link" onClick={onBack}><ArrowLeft size={15} />{backLabel}</button><div className="detail-title"><h1>{task.title}</h1><Status status={task.status} /></div><div className="reader-heading-actions"><button className="button ghost small" aria-expanded={infoOpen} aria-controls="task-information" onClick={() => setInfoOpen(!infoOpen)}>任务信息<ChevronDown size={14} className={infoOpen ? 'is-open' : ''} /></button><button className="button ghost small" onClick={() => void shareTask()}><Link2 size={15} />分享任务</button>{detail.can_delete && <button className={`button ghost small task-delete ${confirmDelete ? 'armed' : ''}`} disabled={deleting} title="删除后任务文件、Rollout 与评审都会一并移除" onClick={() => void removeTask()}>{deleting ? <LoaderCircle size={15} className="spin" /> : <Trash2 size={15} />}{confirmDelete ? '确认删除' : '删除任务'}</button>}</div></div>
      <div className="task-information" id="task-information" hidden={!infoOpen}>
        {task.description && <p>{task.description}</p>}
        <div className="detail-meta"><Avatar name={task.author} size="small" /><span>{task.author}</span><span>{categories[task.category] || task.category}</span><span>{difficultyLabels[task.difficulty]}</span>{task.tags.map(t => <span className="tag" key={t}>{t}</span>)}{task.is_demo && <span className="demo-label">临时任务</span>}<span className="detail-updated">更新于 {relativeTime(task.updated_at)}</span></div>
      </div>
    </div>
    <div className="detail-workspace-bar">
      <div className="reader-source-controls"><div className="detail-source-tabs"><button className={source === 'task' ? 'selected' : ''} aria-pressed={source === 'task'} onClick={() => switchSource('task')}><FileText size={15} />任务文件</button>{rollouts.length > 0 && <button className={source === 'rollout' ? 'selected' : ''} aria-pressed={source === 'rollout'} onClick={() => switchSource('rollout')}><Play size={14} />Rollout 结果<span>{rollouts.length}</span></button>}</div></div>
      <div className="detail-tools"><button ref={translationButton} className={`button ghost small ${translationOpen ? 'is-active' : ''}`} aria-expanded={translationOpen} aria-controls="document-translation" onClick={() => {setTranslationOpen(!translationOpen); if (!translationOpen && matchMedia('(max-width: 900px)').matches) requestAnimationFrame(() => document.getElementById('document-translation')?.scrollIntoView({behavior: 'auto', block: 'start'}));}}><Languages size={16} />双语阅读</button><button ref={reviewsButton} className={`button ghost small ${activePanel === 'reviews' ? 'is-active' : ''}`} aria-expanded={activePanel === 'reviews'} aria-controls="task-reviews-panel" onClick={() => togglePanel('reviews')}><MessageSquare size={15} />评审<span>{reviews.length}</span></button></div>
    </div>
    {source === 'rollout' && rollouts.length > 0 && <div className="rollout-context"><div><GitBranch size={17} /><select aria-label="选择 Rollout 结果" value={rolloutId} onChange={e => {setRolloutId(e.target.value); const chosen = rollouts.find(r => r.id === e.target.value); setSelected(chosen?.files.find(f => f.path.endsWith('result.json')) || chosen?.files[0] || null);}}>{rollouts.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}</select></div>{rollout && <><span className="mono">{rollout.agent || 'Agent 未指定'} · {rollout.model || '模型未指定'}</span><span><Clock3 size={13} />{rollout.duration_seconds != null ? `${Math.round(rollout.duration_seconds)}s` : 'N/A'}</span><span>Reward <strong className="mono">{rollout.reward ?? 'N/A'}</strong></span><span className={`run-status ${rollout.status}`}><span />{rollout.status === 'passed' ? '运行成功' : rollout.status === 'failed' ? '运行失败' : '结果未判定'}</span></>}</div>}
    {menu && <TreeMenu at={menu.at} target={menu.target} onDownload={downloadTarget} onClose={() => setMenu(null)} />}
    {isDrawer && activePanel && <button className="reader-panel-scrim" aria-label="关闭辅助面板" tabIndex={-1} onClick={() => closePanel()} />}
    <div className={`reader-workspace ${activePanel ? `panel-${activePanel}` : ''} ${translationOpen ? 'with-translation' : ''}`}>
      <aside ref={filesPanel} className="reader-panel reader-files-panel" id="task-files-panel" hidden={!filesVisible} inert={!filesVisible} role={isDrawer && activePanel === 'files' ? 'dialog' : undefined} aria-modal={isDrawer && activePanel === 'files' ? true : undefined} aria-labelledby="task-files-heading" tabIndex={-1}>
        <div className="reader-panel-heading"><h2 id="task-files-heading">{source === 'task' ? '任务目录' : '结果目录'}<span>{visibleFiles.length}</span></h2>{isDrawer && <button className="icon-button reader-panel-close" aria-label="关闭目录" onClick={() => closePanel()}><X size={18} /></button>}</div>
        <div className="file-explorer"><div className="explorer-root"><FolderOpen size={15} /><span>{source === 'task' ? task.slug : rollout?.name || 'rollout'}</span>{visibleFiles.length > 0 && <button type="button" className="explorer-download" title="打包下载全部文件" aria-label="打包下载全部文件" onClick={downloadEverything}><FolderDown size={15} /></button>}</div>{visibleFiles.length ? <FileTree files={visibleFiles} selected={selected?.id} onSelect={selectDocument} onMenu={openMenu} /> : <p className="explorer-empty">尚未上传文件</p>}</div>
      </aside>
      <div ref={documentHost} className="reader-document-host" role="region" tabIndex={-1} aria-label="文档内容"><FileReader projectId={project.id} key={`${project.id}:${selected?.id || 'empty'}`} file={selected} files={visibleFiles} onSelect={selectDocument} translationOpen={translationOpen} closeTranslation={() => {setTranslationOpen(false); requestAnimationFrame(() => translationButton.current?.focus({preventScroll: true}));}} user={user} onLogin={requestLogin} /></div>
      <aside ref={reviewsPanel} className="reader-panel reader-reviews-panel" id="task-reviews-panel" hidden={activePanel !== 'reviews'} inert={activePanel !== 'reviews'} role={isDrawer && activePanel === 'reviews' ? 'dialog' : undefined} aria-modal={isDrawer && activePanel === 'reviews' ? true : undefined} aria-labelledby="task-reviews-heading" tabIndex={-1}>
        <div className="reader-panel-heading"><h2 id="task-reviews-heading">评审<span>{reviews.length}</span></h2><button className="icon-button reader-panel-close" aria-label="关闭评审" onClick={() => closePanel()}><X size={18} /></button></div>
        <ReviewPanel projectId={project.id} task={task} reviews={reviews} user={user} onLogin={requestLogin} onDone={async () => {await load(); await onRefresh();}} notify={notify} />
      </aside>
    </div>
  </div>;
}

type TreeTarget = {kind: 'file'; file: Artifact} | {kind: 'folder'; path: string};

/** Right-click menu for the file tree: a file downloads as-is, a folder as a ZIP. */
function TreeMenu({at, target, onDownload, onClose}: {at: {x: number; y: number}; target: TreeTarget; onDownload: (t: TreeTarget) => void; onClose: () => void}) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const away = (event: MouseEvent) => {if (!box.current?.contains(event.target as Node)) onClose();};
    const key = (event: KeyboardEvent) => {if (event.key === 'Escape') onClose();};
    // Capture so a click anywhere, including other tree rows, dismisses first.
    document.addEventListener('mousedown', away, true);
    document.addEventListener('keydown', key);
    window.addEventListener('resize', onClose);
    window.addEventListener('scroll', onClose, true);
    return () => {
      document.removeEventListener('mousedown', away, true);
      document.removeEventListener('keydown', key);
      window.removeEventListener('resize', onClose);
      window.removeEventListener('scroll', onClose, true);
    };
  }, [onClose]);
  useEffect(() => {box.current?.querySelector('button')?.focus({preventScroll: true});}, []);
  const label = target.kind === 'file' ? '下载文件' : '打包下载文件夹';
  const name = target.kind === 'file' ? target.file.path.split('/').pop() : target.path.split('/').pop();
  // Keep the menu inside the viewport when the click lands near an edge.
  const style = {left: Math.min(at.x, window.innerWidth - 220), top: Math.min(at.y, window.innerHeight - 90)} as CSSProperties;
  return <div ref={box} className="tree-menu" style={style} role="menu" aria-label="文件操作">
    <div className="tree-menu-name" title={name}>{name}</div>
    <button type="button" role="menuitem" onClick={() => {onDownload(target); onClose();}}>
      {target.kind === 'file' ? <DownloadIcon size={14} /> : <FolderDown size={14} />}{label}
    </button>
  </div>;
}

function FileTree({files, selected, onSelect, onMenu}: {files: Artifact[]; selected?: string; onSelect: (file: Artifact) => void; onMenu: (event: ReactMouseEvent, target: TreeTarget) => void}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const paths = [...files].sort((a, b) => {if (a.path.endsWith('instruction.md')) return -1; if (b.path.endsWith('instruction.md')) return 1; return a.path.localeCompare(b.path);});
  const renderedDirs = new Set<string>();
  return <div className="file-tree">{paths.flatMap(file => {
    const parts = file.path.split('/').filter(Boolean);
    const rows = [];
    let hidden = false;
    for (let i = 0; i < parts.length - 1; i++) {
      const dir = parts.slice(0, i + 1).join('/');
      if (!renderedDirs.has(dir) && !hidden) {renderedDirs.add(dir); rows.push(<button key={`dir-${dir}`} className="tree-directory" style={{'--tree-indent': `${14 + i * 12}px`} as CSSProperties} aria-expanded={!collapsed.has(dir)} title={`${dir}（右键可打包下载）`} onContextMenu={event => onMenu(event, {kind: 'folder', path: dir})} onClick={() => setCollapsed(prev => {const next = new Set(prev); if (next.has(dir)) next.delete(dir); else next.add(dir); return next;})}>{collapsed.has(dir) ? <ChevronRight size={12} /> : <ChevronDown size={12} />}<FolderClosed size={14} /><span>{parts[i]}</span></button>);}
      if (collapsed.has(dir)) hidden = true;
    }
    if (!hidden) rows.push(<button key={file.id} className={`tree-file ${selected === file.id ? 'selected' : ''}`} style={{'--tree-indent': `${24 + (parts.length - 1) * 12}px`} as CSSProperties} onClick={() => onSelect(file)} onContextMenu={event => onMenu(event, {kind: 'file', file})} title={`${file.path}（右键可下载）`} aria-current={selected === file.id ? 'true' : undefined}><FileIcon path={file.path} size={14} /><span>{parts[parts.length - 1]}</span></button>);
    return rows;
  })}</div>;
}

function FileReader({projectId, file, files, onSelect, translationOpen, closeTranslation, user, onLogin}: {projectId: string; file: Artifact|null; files: Artifact[]; onSelect: (file: Artifact) => void; translationOpen: boolean; closeTranslation: () => void; user: User|null; onLogin: () => void}) {
  const [content, setContent] = useState('');
  const [busy, setBusy] = useState(!!file);
  const [error, setError] = useState('');
  const [truncated, setTruncated] = useState(false);
  const [raw, setRaw] = useState(false);
  const [translated, setTranslated] = useState('');
  const [translating, setTranslating] = useState(false);
  const [translationError, setTranslationError] = useState('');
  const [language, setLanguage] = useState('zh');
  const abortRef = useRef<AbortController|null>(null);
  const downloadUrl = file ? projectPath(`/api/files/${encodeURIComponent(file.id)}/download`, projectId) : undefined;
  const isImage = !!file?.mime_type?.startsWith('image/') && !/svg/i.test(file.mime_type);
  const isBinary = !!file && !isImage && /\.(pdf|zip|gz|tar|parquet|npy|npz|pkl|pickle|pt|bin|woff2?|mp4|mp3|wav|exe|dll)$/i.test(file.path);
  useEffect(() => {
    if (!file || isImage || isBinary) {setBusy(false); return;}
    const controller = new AbortController();
    api<{content: string; truncated: boolean}>(projectPath(`/files/${file.id}/content`, projectId), {signal: controller.signal}).then(result => {setContent(result.content); setTruncated(result.truncated);}).catch(e => {if (e.name !== 'AbortError') setError(e.message);}).finally(() => {if (!controller.signal.aborted) setBusy(false);});
    return () => controller.abort();
  }, [file, isImage, isBinary, projectId]);
  useEffect(() => () => abortRef.current?.abort(), []);
  useEffect(() => {if (!translationOpen) {abortRef.current?.abort(); setTranslating(false);}}, [translationOpen]);
  async function translate() {
    if (!user) {onLogin(); return;}
    if (!file) return;
    abortRef.current?.abort(); const controller = new AbortController(); abortRef.current = controller;
    setTranslating(true); setTranslated(''); setTranslationError('');
    try {
      const response = await fetch('/api/translate', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({project_id: projectId, file_id: file.id, target_language: language}), signal: controller.signal});
      if (!response.ok) {const body = await response.json().catch(() => null); throw new Error(body?.detail || '翻译服务连接失败');}
      if (!response.body) throw new Error('浏览器不支持流式读取');
      const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; let complete = false;
      while (true) {
        const {value, done} = await reader.read(); buffer += decoder.decode(value, {stream: !done});
        const lines = buffer.split('\n'); buffer = lines.pop() || '';
        for (const line of lines) {if (!line.startsWith('data:')) continue; const rawEvent = line.slice(5).trim(); if (rawEvent === '[DONE]') {complete = true; continue;} if (!rawEvent) continue; const event = JSON.parse(rawEvent); if (event.error) throw new Error(event.error); if (event.text) setTranslated(prev => prev + event.text); if (event.done) complete = true;}
        if (done) {if (!complete) throw new Error('翻译连接中断，请重新翻译。'); break;}
      }
    } catch (e) {if ((e as Error).name !== 'AbortError') setTranslationError((e as Error).message);} finally {if (!controller.signal.aborted) setTranslating(false);}
  }
  const markdown = !!file && /\.(md|markdown)$/i.test(file.path);
  let displayed = content;
  if (file?.path.endsWith('.json')) {try {displayed = JSON.stringify(JSON.parse(content), null, 2);} catch {/* Preserve malformed JSON as an original document. */}}
  return <section className="document-reader"><div className="document-toolbar"><div><FileIcon path={file?.path || ''} /><span title={file?.path}>{file?.path || '选择文件'}</span>{file && <small>{sizeLabel(file.size)}</small>}</div><div>{markdown && <div className="view-switch"><button className={!raw ? 'selected' : ''} onClick={() => setRaw(false)} title="预览" aria-label="预览文档"><Eye size={14} /></button><button className={raw ? 'selected' : ''} onClick={() => setRaw(true)} title="源文件" aria-label="查看源文件"><Code2 size={14} /></button></div>}{file && <a className="icon-button" href={downloadUrl} download title="下载原始文件" aria-label="下载原始文件"><Download size={16} /></a>}</div></div>
    <div className={`reading-columns ${translationOpen ? 'translated' : ''}`}><div className="original-column">{translationOpen && <div className="reading-label"><span>原文</span><span>{markdown ? 'Markdown' : 'Source'}</span></div>}{busy ? <Loading text="读取文件…" /> : !file ? <Empty icon={<FileText size={28} />} title="选择文件" description="" /> : error ? <Empty title="此文件无法预览" description={error} action={<a className="button secondary" href={downloadUrl} download><Download size={15} />下载原始文件</a>} /> : isImage ? <div className="image-preview"><img src={downloadUrl} alt={file.path} /></div> : isBinary ? <Empty icon={<FileText size={28} />} title="下载文件以查看" description={`${file.path} · ${sizeLabel(file.size)}`} action={<a className="button secondary" href={downloadUrl} download><Download size={15} />下载原始文件</a>} /> : <>{truncated && <div className="preview-notice">仅预览前 300 KB，完整文件请下载。</div>}{markdown && !raw ? <article className="markdown-content"><ArtifactMarkdown projectId={projectId} content={content} file={file} files={files} onSelect={onSelect} /></article> : <pre className="code-preview"><code>{displayed || '（空文件）'}</code></pre>}</>}</div>
      {translationOpen && <div className="translation-column" id="document-translation"><div className="reading-label"><span><Languages size={14} />译文</span><div><select aria-label="翻译目标语言" value={language} disabled={translating} onChange={e => {setLanguage(e.target.value); setTranslated(''); setTranslationError('');}}><option value="zh">简体中文</option><option value="en">English</option></select><button className="icon-button" onClick={closeTranslation} aria-label="关闭翻译"><X size={14} /></button></div></div>{translated && <article className="markdown-content">{file && <ArtifactMarkdown projectId={projectId} content={translated} file={file} files={files} onSelect={onSelect} />}{translating && <span className="stream-cursor" />}</article>}{translationError && <div className="translation-error" role="alert">{translationError}</div>}{!translated && !translating && <div className="translation-start"><button className="button primary small" disabled={!file || busy || !!error || isImage || isBinary} onClick={() => void translate()}><Languages size={15} />{user ? '翻译当前文件' : '登录并翻译'}</button><small>文件文本将发送至平台翻译服务</small></div>}{translating && !translated && <Loading text="翻译中…" />}{(translated || translating) && <div className="translation-footer">{translating ? <button className="button secondary small" onClick={() => {abortRef.current?.abort(); setTranslating(false);}}>停止翻译</button> : <><button className="text-button" onClick={() => void translate()}><RefreshCw size={12} />重新翻译</button></>}</div>}</div>}
    </div>
  </section>;
}

function ReviewPanel({projectId, task, reviews, user, onLogin, onDone, notify}: {projectId: string; task: Task; reviews: Review[]; user: User|null; onLogin: () => void; onDone: () => Promise<void>; notify: (text: string) => void}) {
  const [verdict, setVerdict] = useState<Review['verdict']>('comment');
  const [body, setBody] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!user) {onLogin(); return;}
    setBusy(true); setError('');
    try {await post(projectPath(`/tasks/${task.id}/reviews`, projectId), {body: body.trim(), verdict}); setBody(''); setVerdict('comment'); await onDone(); notify('评审已提交');}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <div className="review-panel"><div className="review-messages">{reviews.length ? [...reviews].sort((a, b) => a.created_at.localeCompare(b.created_at)).map(review => <div className="review-message" key={review.id}><div className="review-message-heading"><Avatar name={review.author} size="small" /><strong>{review.author}</strong><time>{relativeTime(review.created_at)}</time></div>{review.verdict !== 'comment' && <div className={`review-verdict ${review.verdict}`}>{review.verdict === 'approved' ? <CircleCheck size={13} /> : <RefreshCw size={13} />}{review.verdict === 'approved' ? '合格' : '需修改'}</div>}<p>{review.body}</p></div>) : <div className="no-reviews"><strong>暂无评审</strong></div>}</div><form className="review-compose" onSubmit={submit}><label htmlFor="review-body">评审意见</label><textarea id="review-body" placeholder="填写评审意见…" value={body} onChange={e => setBody(e.target.value)} rows={4} required maxLength={10000} disabled={!user} /><div className="verdict-options">{(['comment', 'approved', 'changes_requested'] as const).map(value => <button key={value} type="button" aria-pressed={verdict === value} className={verdict === value ? 'selected' : ''} onClick={() => setVerdict(value)}>{value === 'comment' ? <MessageSquare size={12} /> : value === 'approved' ? <Check size={12} /> : <RefreshCw size={12} />}{value === 'comment' ? '评论' : value === 'approved' ? '通过' : '需修改'}</button>)}</div>{error && <p className="form-error" role="alert">{error}</p>}{user ? <button className="button primary full small" disabled={busy || !body.trim()}>{busy ? <LoaderCircle size={14} className="spin" /> : <Send size={13} />}提交评审</button> : <button type="button" className="button primary full small" onClick={onLogin}>登录以参与评审<ArrowUpRight size={13} /></button>}</form></div>;
}
