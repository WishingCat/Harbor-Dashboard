let activeLocks = 0;
let previousOverflow = '';

/** Navigation, dialogs and responsive panels can overlap without leaving a stale lock. */
export function lockPageScroll() {
  if (activeLocks === 0) {
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
  }
  activeLocks++;
  let released = false;
  return () => {
    if (released) return;
    released = true;
    activeLocks--;
    if (activeLocks === 0) document.body.style.overflow = previousOverflow;
  };
}
