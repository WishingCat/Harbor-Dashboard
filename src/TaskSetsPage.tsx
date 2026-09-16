import { useEffect, useState } from 'react';
import { ChevronRight, FolderClosed, LoaderCircle, Plus, Trash2 } from 'lucide-react';
import { api, projectPath } from './api';
import { Empty } from './components';
import type { Project, TaskSet } from './types';

export default function TaskSetsPage({project, taskSets, onOpen, onCreate, onDeleted, notify}: {
  project: Project;
  taskSets: TaskSet[];
  onOpen: (id: string) => void;
  onCreate: () => void;
  onDeleted: () => Promise<void> | void;
  notify: (message: string) => void;
}) {
  const [confirming, setConfirming] = useState('');
  const [busy, setBusy] = useState('');
  // Deleting cannot be undone, so the button arms first and disarms on its own.
  useEffect(() => {
    if (!confirming) return;
    const timer = setTimeout(() => setConfirming(''), 5000);
    return () => clearTimeout(timer);
  }, [confirming]);
  async function remove(taskSet: TaskSet) {
    if (busy) return;
    if (confirming !== taskSet.id) {setConfirming(taskSet.id); return;}
    setConfirming(''); setBusy(taskSet.id);
    try {
      await api(projectPath(`/task-sets/${encodeURIComponent(taskSet.id)}`, project.id), {method: 'DELETE'});
      notify('任务集已删除');
      await onDeleted();
    } catch (e) {notify((e as Error).message);}
    finally {setBusy('');}
  }
  const createButton = <button type="button" className="button primary" onClick={onCreate}><Plus size={18} />新建任务集</button>;
  return <div className="page-content task-sets-page">
    <div className="page-heading"><h1>任务集</h1>{taskSets.length > 0 && <div className="heading-actions">{createButton}</div>}</div>
    <section className="task-sets-section" aria-label={`${project.name}的任务集`}>
      {taskSets.length ? <table className="task-sets-table">
        <thead><tr><th scope="col">任务集名称</th><th scope="col">任务数</th><th scope="col">待评审</th><th aria-label="删除任务集" /><th aria-label="打开任务集" /></tr></thead>
        <tbody>{taskSets.map(taskSet => <tr key={taskSet.id} onClick={() => onOpen(taskSet.id)}>
          <td><button type="button" className="task-set-name" title={taskSet.name} onClick={event => {event.stopPropagation(); onOpen(taskSet.id);}}><FolderClosed size={20} strokeWidth={1.7} aria-hidden="true" /><span>{taskSet.name}</span></button></td>
          <td className="task-set-count">{taskSet.tasks_count.toLocaleString()}</td>
          <td className={`task-set-count ${taskSet.pending_count > 0 ? 'has-pending' : ''}`}>{taskSet.pending_count.toLocaleString()}</td>
          <td className="task-set-actions">{taskSet.can_delete && <button type="button" className={`task-set-delete ${confirming === taskSet.id ? 'armed' : ''}`} disabled={busy === taskSet.id} title={taskSet.tasks_count > 0 ? '任务集非空，需先删除其中的任务' : '删除任务集'} aria-label={confirming === taskSet.id ? `确认删除 ${taskSet.name}` : `删除 ${taskSet.name}`} onClick={event => {event.stopPropagation(); void remove(taskSet);}}>{busy === taskSet.id ? <LoaderCircle size={14} className="spin" /> : confirming === taskSet.id ? '确认删除' : <Trash2 size={15} />}</button>}</td>
          <td><ChevronRight size={16} className="task-set-chevron" aria-hidden="true" /></td>
        </tr>)}</tbody>
      </table> : <Empty icon={<FolderClosed size={26} />} title="还没有任务集" description="" action={createButton} />}
    </section>
  </div>;
}
