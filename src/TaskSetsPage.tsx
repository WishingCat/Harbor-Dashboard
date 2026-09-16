import { ChevronRight, FolderClosed, Plus } from 'lucide-react';
import { Empty } from './components';
import type { Project, TaskSet } from './types';

export default function TaskSetsPage({project, taskSets, onOpen, onCreate}: {
  project: Project;
  taskSets: TaskSet[];
  onOpen: (id: string) => void;
  onCreate: () => void;
}) {
  const createButton = <button type="button" className="button primary" onClick={onCreate}><Plus size={18} />新建任务集</button>;
  return <div className="page-content task-sets-page">
    <div className="page-heading"><h1>任务集</h1>{taskSets.length > 0 && <div className="heading-actions">{createButton}</div>}</div>
    <section className="task-sets-section" aria-label={`${project.name}的任务集`}>
      {taskSets.length ? <table className="task-sets-table">
        <thead><tr><th scope="col">任务集名称</th><th scope="col">任务数</th><th scope="col">待评审</th><th aria-label="打开任务集" /></tr></thead>
        <tbody>{taskSets.map(taskSet => <tr key={taskSet.id} onClick={() => onOpen(taskSet.id)}>
          <td><button type="button" className="task-set-name" title={taskSet.name} onClick={event => {event.stopPropagation(); onOpen(taskSet.id);}}><FolderClosed size={20} strokeWidth={1.7} aria-hidden="true" /><span>{taskSet.name}</span></button></td>
          <td className="task-set-count">{taskSet.tasks_count.toLocaleString()}</td>
          <td className={`task-set-count ${taskSet.pending_count > 0 ? 'has-pending' : ''}`}>{taskSet.pending_count.toLocaleString()}</td>
          <td><ChevronRight size={16} className="task-set-chevron" aria-hidden="true" /></td>
        </tr>)}</tbody>
      </table> : <Empty icon={<FolderClosed size={26} />} title="还没有任务集" description="" action={createButton} />}
    </section>
  </div>;
}
