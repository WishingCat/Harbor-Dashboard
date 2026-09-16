import { useId, useRef, useState, type FormEvent } from 'react';
import { LoaderCircle } from 'lucide-react';
import { post } from './api';
import { Modal } from './components';
import type { Project, TaskSet } from './types';

export function HierarchyCreateModal({kind, project, onClose, onSuccess}: {
  kind: 'project' | 'task-set';
  project?: Project;
  onClose: () => void;
  onSuccess: (item: Project | TaskSet) => void;
}) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const submitting = useRef(false);
  const input = useRef<HTMLInputElement>(null);
  const errorId = useId();
  const label = kind === 'project' ? '项目' : '任务集';
  const missingProject = kind === 'task-set' && !project;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting.current) return;
    const cleanName = name.trim();
    if (!cleanName) {setError(`请输入${label}名称。`); input.current?.focus(); return;}
    if (missingProject) {setError('请先选择所属项目。'); return;}
    submitting.current = true; setBusy(true); setError('');
    try {
      if (kind === 'project') {
        const result = await post<{project: Project}>('/projects', {name: cleanName});
        onSuccess(result.project);
      } else {
        const result = await post<{task_set: TaskSet}>('/task-sets', {project_id: project!.id, name: cleanName});
        onSuccess(result.task_set);
      }
    } catch (e) {setError((e as Error).message);}
    finally {submitting.current = false; setBusy(false);}
  }

  return <Modal title={`新建${label}`} onClose={() => {if (!submitting.current) onClose();}}>
    <form className="form-stack hierarchy-create" onSubmit={submit} aria-busy={busy}>
      {kind === 'task-set' && <div className="hierarchy-project"><span>所属项目</span><strong>{project?.name || '未选择项目'}</strong></div>}
      <label>{label}名称<input ref={input} name="name" value={name} onChange={event => setName(event.target.value)} required maxLength={80} disabled={busy} autoComplete="off" placeholder={`输入${label}名称`} aria-describedby={error || missingProject ? errorId : undefined} /></label>
      {(error || missingProject) && <p id={errorId} className="form-error" role="alert">{error || '请先选择所属项目。'}</p>}
      <div className="modal-footer"><button type="button" className="button secondary" disabled={busy} onClick={onClose}>取消</button><button type="submit" className="button primary" disabled={busy || missingProject}>{busy ? <><LoaderCircle size={16} className="spin" />正在创建…</> : `创建${label}`}</button></div>
    </form>
  </Modal>;
}
