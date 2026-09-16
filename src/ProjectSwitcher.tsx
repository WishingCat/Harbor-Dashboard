import { useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Check, ChevronDown, FolderClosed, Plus } from 'lucide-react';
import type { Project } from './types';
import './project-switcher.css';

export function ProjectSwitcher({projects, projectId, onChange, onCreate, active = true}: {
  projects: Project[];
  projectId: string;
  onChange: (id: string) => void;
  onCreate: () => void;
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [placement, setPlacement] = useState({above: false, maxHeight: 360});
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const items = useRef<(HTMLButtonElement | null)[]>([]);
  const openingIndex = useRef(0);
  const typeahead = useRef({text: '', time: 0});
  const menuId = useId();
  const expanded = active && open;
  const projectName = projects.find(project => project.id === projectId)?.name || '选择项目';

  function close(restoreFocus = false) {
    // Restore synchronously, before a caller can mount its own modal.
    if (restoreFocus) trigger.current?.focus({preventScroll: true});
    setOpen(false);
    typeahead.current = {text: '', time: 0};
  }

  function focusItem(index: number) {
    const count = projects.length + 1;
    items.current[(index + count) % count]?.focus({preventScroll: true});
    const item = items.current[(index + count) % count];
    const list = item?.closest<HTMLElement>('.project-picker-options');
    if (list && item) {
      const itemBox = item.getBoundingClientRect(), listBox = list.getBoundingClientRect();
      if (itemBox.top < listBox.top) list.scrollTop -= listBox.top - itemBox.top;
      else if (itemBox.bottom > listBox.bottom) list.scrollTop += itemBox.bottom - listBox.bottom;
    }
  }

  function show(index = 0) {
    if (!active) return;
    openingIndex.current = index;
    if (expanded) focusItem(index);
    else setOpen(true);
  }

  useLayoutEffect(() => {
    if (!active) setOpen(false);
  }, [active]);
  useEffect(() => {setOpen(false);}, [projectId]);

  useLayoutEffect(() => {
    if (!expanded) return;
    function position() {
      const button = trigger.current;
      if (!button) return;
      const bounds = button.getBoundingClientRect();
      const sidebarBounds = button.closest('.sidebar')?.getBoundingClientRect();
      const below = Math.min(window.innerHeight, sidebarBounds?.bottom ?? window.innerHeight) - bounds.bottom - 12;
      const above = bounds.top - Math.max(0, sidebarBounds?.top ?? 0) - 12;
      const placeAbove = below < 160 && above > below;
      setPlacement({above: placeAbove, maxHeight: Math.max(0, Math.min(360, placeAbove ? above : below))});
    }
    position();
    focusItem(openingIndex.current);
    window.addEventListener('resize', position);
    window.addEventListener('scroll', position, true);
    return () => {window.removeEventListener('resize', position); window.removeEventListener('scroll', position, true);};
  }, [expanded]);

  useLayoutEffect(() => {
    if (!expanded) return;
    const index = items.current.findIndex(item => item === document.activeElement);
    if (index >= 0) focusItem(index);
  }, [expanded, placement.above, placement.maxHeight]);

  useEffect(() => {
    if (!expanded) return;
    function outside(event: Event) {
      if (event.target instanceof Node && !root.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener('pointerdown', outside, true);
    document.addEventListener('focusin', outside);
    return () => {document.removeEventListener('pointerdown', outside, true); document.removeEventListener('focusin', outside);};
  }, [expanded]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.nativeEvent.isComposing) return;
    if (!expanded) {
      if (event.target === trigger.current && (event.key === 'ArrowDown' || event.key === 'ArrowUp')) {
        event.preventDefault(); event.stopPropagation(); show(event.key === 'ArrowUp' ? projects.length : 0);
      }
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); close(true); return;
    }
    if (event.key === 'Tab') {
      event.preventDefault(); event.stopPropagation();
      const scope = trigger.current?.closest('dialog[open]') || document;
      const focusable = Array.from(scope.querySelectorAll<HTMLElement>('button, a[href], input, select, textarea, [tabindex]'))
        .filter(element => element.tabIndex >= 0 && !element.matches(':disabled') && element.getClientRects().length && !element.closest('[inert], [hidden]'));
      const index = focusable.indexOf(trigger.current!);
      const next = focusable[index + (event.shiftKey ? -1 : 1)] || (event.shiftKey ? focusable.at(-1) : focusable[0]);
      close(); next?.focus({preventScroll: true}); return;
    }
    const index = Math.max(0, items.current.findIndex(item => item === document.activeElement));
    if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
      event.preventDefault(); event.stopPropagation();
      focusItem(event.key === 'Home' ? 0 : event.key === 'End' ? projects.length : index + (event.key === 'ArrowDown' ? 1 : -1));
      return;
    }
    if (event.key.length === 1 && event.key !== ' ' && !event.ctrlKey && !event.metaKey && !event.altKey) {
      const now = performance.now();
      const text = (now - typeahead.current.time < 600 ? typeahead.current.text : '') + event.key.toLocaleLowerCase();
      typeahead.current = {text, time: now};
      const labels = ['新建项目', ...projects.map(project => project.name)].map(label => label.toLocaleLowerCase());
      const prefix = [...text].every(character => character === text[0]) ? text[0] : text;
      for (let offset = 1; offset <= labels.length; offset++) {
        const next = (index + offset) % labels.length;
        if (labels[next].startsWith(prefix)) {event.preventDefault(); event.stopPropagation(); focusItem(next); break;}
      }
    }
  }

  return <div className="project-picker" ref={root} onKeyDown={onKeyDown}>
    <button ref={trigger} type="button" className="project-picker-trigger" aria-label="选择项目" title={projectName} aria-haspopup="menu" aria-expanded={expanded} aria-controls={expanded ? menuId : undefined} disabled={!active} onClick={() => {if (expanded) close(); else show();}}>
      <FolderClosed size={17} aria-hidden="true" /><span>{projectName}</span><ChevronDown size={15} aria-hidden="true" />
    </button>
    {expanded && <div id={menuId} role="menu" aria-label="选择项目" className={`project-picker-menu ${placement.above ? 'opens-above' : ''}`} style={{maxHeight: placement.maxHeight}}>
      <button ref={element => {items.current[0] = element;}} type="button" role="menuitem" tabIndex={-1} className="project-picker-item project-picker-create" onClick={() => {close(true); onCreate();}}><Plus size={17} aria-hidden="true" /><span>新建项目</span></button>
      {projects.length > 0 && <><div className="project-picker-separator" role="separator" /><div className="project-picker-options" role="group" aria-label="已有项目">{projects.map((project, index) => <button ref={element => {items.current[index + 1] = element;}} key={project.id} type="button" role="menuitemradio" aria-checked={project.id === projectId} tabIndex={-1} className="project-picker-item" title={project.name} onClick={() => {close(true); onChange(project.id);}}><span>{project.name}</span>{project.id === projectId && <Check size={16} aria-hidden="true" />}</button>)}</div></>}
    </div>}
  </div>;
}

export default ProjectSwitcher;
