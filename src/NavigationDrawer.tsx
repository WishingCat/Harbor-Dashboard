import { useLayoutEffect, useRef, type ReactNode } from 'react';
import { lockPageScroll } from './pageScrollLock';

/** Desktop workspace navigation is non-modal; the reader and small screens use a native drawer. */
export function NavigationDrawer({open, docked, onClose, children}: {open: boolean; docked: boolean; onClose: () => void; children: ReactNode}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useLayoutEffect(() => {
    if (!open || docked) return;
    const element = dialog.current;
    if (!element) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    element.showModal();
    const unlock = lockPageScroll();
    return () => {
      if (element.open) element.close();
      unlock();
      if (previousFocus?.isConnected && previousFocus.getClientRects().length && !previousFocus.closest('[inert], [hidden]')) previousFocus.focus({preventScroll: true});
    };
  }, [open, docked]);
  if (docked) return <div id="workspace-navigation" className="navigation-persistent">{children}</div>;
  return <dialog ref={dialog} id="workspace-navigation" className="navigation-drawer" aria-label="工作区导航"
    onCancel={event => {event.preventDefault(); onClose();}}
    onClick={event => {if (event.target === event.currentTarget) onClose();}}>
    {children}
  </dialog>;
}
