import { useRef, useState } from 'react';
import type { FormEvent, InputHTMLAttributes } from 'react';
import { ArrowRight, FileArchive, FolderOpen, LoaderCircle, Upload, X } from 'lucide-react';
import { api, post, sizeLabel } from './api';
import { Modal } from './components';
import type { Project, Task, TaskSet, User } from './types';

export function AuthModal({onClose, onSuccess}: {onClose: () => void; onSuccess: (user: User) => void}) {
  const [register, setRegister] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setError(''); setBusy(true);
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {const result = await post<{user: User}>(register ? '/auth/register' : '/auth/login', data); onSuccess(result.user);}
    catch (e) {setError((e as Error).message);} finally {setBusy(false);}
  }
  return <Modal title={register ? '创建账号' : '登录 Harbor'} onClose={onClose}><form onSubmit={submit} className="form-stack">{register && <label>你的名字<input name="name" autoComplete="name" required maxLength={60} placeholder="评审时展示的名字" /></label>}<label>{register ? '邮箱地址' : '用户名或邮箱'}<input name={register ? 'email' : 'username'} type={register ? 'email' : 'text'} autoComplete={register ? 'email' : 'username'} required placeholder={register ? 'you@team.com' : '输入姓名拼音或邮箱'} /></label><label>密码<input name="password" type="password" autoComplete={register ? 'new-password' : 'current-password'} required minLength={register ? 8 : 1} maxLength={128} placeholder={register ? '至少 8 个字符' : '输入你的密码'} /></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="button primary full" disabled={busy}>{busy ? <LoaderCircle className="spin" size={16} /> : <>{register ? '创建账号' : '登录工作台'}<ArrowRight size={16} /></>}</button><p className="auth-switch">{register ? '已有账号？' : '还没有账号？'} <button type="button" className="text-button" onClick={() => {setRegister(!register); setError('');}}>{register ? '登录' : '创建账号'}</button></p></form></Modal>;
}

export function UploadModal({project, taskSet, onClose, onSuccess}: {project: Project; taskSet: TaskSet; onClose: () => void; onSuccess: (id: string) => void}) {
  const [files, setFiles] = useState<File[]>([]);
  const [description, setDescription] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [drag, setDrag] = useState(false);
  const uploading = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const paths = files.map(file => file.webkitRelativePath || file.name);
  const folder = !!files[0]?.webkitRelativePath;
  const taskName = folder ? paths[0].split('/')[0] : files[0]?.name.replace(/\.zip$/i, '') || '';
  const totalSize = files.reduce((total, file) => total + file.size, 0);

  function selectFiles(selected: FileList|null, kind: 'zip'|'folder') {
    if (uploading.current || !selected?.length) return;
    const chosen = Array.from(selected);
    if (kind === 'zip' && (chosen.length !== 1 || !/\.zip$/i.test(chosen[0].name))) {
      setError('请上传单个 ZIP 文件；文件夹请使用“选择文件夹”。'); return;
    }
    if (kind === 'zip' && !chosen[0].name.replace(/\.zip$/i, '').trim()) {
      setError('ZIP 文件名需要包含任务名称。'); return;
    }
    if (kind === 'folder') {
      if (chosen.length > 1000) {
        setError('文件夹超过 1000 个文件，请打包为 ZIP 后上传（ZIP 最多包含 2000 个文件）。'); return;
      }
      const root = chosen[0].webkitRelativePath.split('/')[0];
      if (!root || chosen.some(file => !file.webkitRelativePath.startsWith(`${root}/`))) {
        setError('请选择一个完整的任务文件夹。'); return;
      }
    }
    if (chosen.reduce((total, file) => total + file.size, 0) > 50 * 1024 * 1024) {
      setError('上传总大小不能超过 50 MB。'); return;
    }
    setFiles(chosen); setError('');
  }
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (uploading.current) return;
    if (!files.length) {setError('请选择 ZIP 文件或任务文件夹。'); return;}
    uploading.current = true; setBusy(true); setDrag(false); setError('');
    const data = new FormData();
    data.append('project_id', project.id);
    data.append('task_set_id', taskSet.id);
    data.append('description', description);
    files.forEach(file => data.append('files', file));
    data.append('paths', JSON.stringify(paths));
    try {const result = await api<{task: Task}>('/tasks', {method: 'POST', body: data}); onSuccess(result.task.id);}
    catch (e) {setError((e as Error).message);}
    finally {uploading.current = false; setBusy(false);}
  }
  return <Modal title="上传任务" subtitle={`${project.name} / ${taskSet.name}`} onClose={() => {if (!uploading.current) onClose();}}>
    <form className="form-stack upload-simple" onSubmit={submit} aria-busy={busy}>
      <div className={`dropzone upload-dropzone ${drag ? 'dragging' : ''}`} onDragOver={event => {event.preventDefault(); if (!uploading.current) setDrag(true);}} onDragLeave={event => {if (!event.currentTarget.contains(event.relatedTarget as Node|null)) setDrag(false);}} onDrop={event => {event.preventDefault(); setDrag(false); selectFiles(event.dataTransfer.files, 'zip');}}>
        {files.length ? <div className="upload-selection" role="status">{folder ? <FolderOpen size={24} /> : <FileArchive size={24} />}<div><span>任务名称</span><strong>{taskName}</strong><small>{folder ? `${files.length} 个文件 · ` : ''}{sizeLabel(totalSize)} / 50 MB</small></div><button type="button" className="icon-button" aria-label="移除已选文件" disabled={busy} onClick={() => {if (!uploading.current) {setFiles([]); setError('');}}}><X size={17} /></button></div> : <><FileArchive size={28} className="upload-file-icon" /><strong>拖放 ZIP 文件</strong><p>ZIP 或文件夹，最大 50 MB</p></>}
        <div className="button-row"><button type="button" className="button secondary small" disabled={busy} onClick={() => fileInput.current?.click()}><FileArchive size={15} />选择 ZIP</button><button type="button" className="button secondary small" disabled={busy} onClick={() => folderInput.current?.click()}><FolderOpen size={15} />选择文件夹</button></div>
        <input ref={fileInput} type="file" accept=".zip" hidden disabled={busy} onChange={event => {selectFiles(event.target.files, 'zip'); event.currentTarget.value = '';}} />
        <input ref={folderInput} type="file" multiple hidden disabled={busy} {...({webkitdirectory: '', directory: ''} as InputHTMLAttributes<HTMLInputElement>)} onChange={event => {selectFiles(event.target.files, 'folder'); event.currentTarget.value = '';}} />
      </div>
      <label>任务简介<span className="label-hint">可选</span><textarea name="description" value={description} onChange={event => setDescription(event.target.value)} disabled={busy} rows={3} maxLength={2000} placeholder="简要说明任务内容" /></label>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="modal-footer"><button type="submit" className="button primary" disabled={busy || !files.length}>{busy ? <><LoaderCircle size={16} className="spin" />正在上传…</> : <><Upload size={16} />上传任务</>}</button></div>
    </form>
  </Modal>;
}
