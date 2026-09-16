import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { Anchor, Check, Circle, CircleCheck, CircleDashed, FileCode2, FileJson2, FileText, LoaderCircle, MessageSquare, RefreshCw, X } from 'lucide-react';
import type { TaskStatus } from './types';
import { lockPageScroll } from './pageScrollLock';

export const categories: Record<string, string> = {'software-engineering': '软件工程', 'data-science': '数据科学', 'system-administration': '系统运维', 'scientific-computing': '科学计算'};
export const statusLabels: Record<TaskStatus, string> = {pending: '待评审', approved: '已通过', changes_requested: '需修改'};
export const difficultyLabels = {easy: '简单', medium: '中等', hard: '困难'};
export function Brand({small = false}: {small?: boolean}) {
  return <div className={`brand ${small ? 'small' : ''}`}><span className="brand-symbol" aria-hidden="true"><Anchor size={20} strokeWidth={1.8} /></span>{!small && <span className="brand-wordmark">Harbor Dashboard</span>}</div>;
}
export function Status({status}: {status: TaskStatus}) {
  const Icon = status === 'approved' ? CircleCheck : status === 'changes_requested' ? RefreshCw : CircleDashed;
  return <span className={`status ${status}`}><Icon size={13} />{statusLabels[status]}</span>;
}
export function Avatar({name, size = 'normal'}: {name: string; size?: 'normal'|'small'}) {
  const shade = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % 4;
  return <span className={`avatar avatar-${shade} ${size}`} aria-hidden="true">{name.slice(0, 1).toUpperCase()}</span>;
}
export function FileIcon({path, size = 16}: {path: string; size?: number}) {
  return path.endsWith('.json') || path.endsWith('.toml') ? <FileJson2 size={size} /> : /\.(py|sh|js|ts|bat)$/.test(path) || path.includes('Dockerfile') ? <FileCode2 size={size} /> : <FileText size={size} />;
}
export function Empty({icon, title, description, action}: {icon?: ReactNode; title: string; description: string; action?: ReactNode}) {
  return <div className="empty-state"><div className="empty-icon">{icon || <MessageSquare size={25} />}</div><h3>{title}</h3><p>{description}</p>{action}</div>;
}
export function Loading({text = '正在加载…'}: {text?: string}) {
  return <div className="loading" role="status"><LoaderCircle size={20} className="spin" />{text}</div>;
}
export function WorkspaceSkeleton() {
  return <div className="workspace-skeleton" role="status" aria-label="正在加载工作区"><div className="skeleton-line skeleton-title" /><div className="skeleton-metrics">{Array.from({length: 4}, (_, i) => <div key={i}><div className="skeleton-line" /><div className="skeleton-line skeleton-value" /></div>)}</div><div className="skeleton-line skeleton-toolbar" />{Array.from({length: 6}, (_, i) => <div className="skeleton-row" key={i}><div className="skeleton-line skeleton-icon" /><div><div className="skeleton-line" /><div className="skeleton-line skeleton-subtitle" /></div><div className="skeleton-line skeleton-badge" /></div>)}</div>;
}
export function Modal({title, subtitle, onClose, children, wide = false}: {title: string; subtitle?: string; onClose: () => void; children: ReactNode; wide?: boolean}) {
  const dialog = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const unlock = lockPageScroll();
    dialog.current?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onCloseRef.current();
      if (event.key === 'Tab') {
        const focusable = dialog.current?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]');
        if (!focusable?.length) return;
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {event.preventDefault(); last.focus();}
        else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) {event.preventDefault(); first.focus();}
      }
    }
    document.addEventListener('keydown', onKey);
    return () => {unlock(); document.removeEventListener('keydown', onKey); if (previous?.isConnected) previous.focus();};
  }, []);
  return <div className="modal-backdrop" onMouseDown={e => {if (e.target === e.currentTarget) onClose();}}><div ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="modal-title" className={`modal ${wide ? 'wide' : ''}`}><div className="modal-header"><div><h2 id="modal-title">{title}</h2>{subtitle && <p>{subtitle}</p>}</div><button type="button" className="icon-button" onClick={onClose} aria-label="关闭弹窗"><X size={19} /></button></div>{children}</div></div>;
}
export function CheckItem({children, done}: {children: ReactNode; done: boolean}) {
  return <span className={`check-item ${done ? 'done' : ''}`}>{done ? <Check size={14} /> : <Circle size={14} />}{children}</span>;
}
