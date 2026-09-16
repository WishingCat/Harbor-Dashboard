import { useEffect, useState } from 'react';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'harbor-theme';
// New visitors start in light mode. The system preference never selects dark on
// its own; only an explicit toggle, remembered per browser, switches the theme.
const DEFAULT_THEME: Theme = 'light';

function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {return null;}
}

export function useTheme() {
  const [preference, setPreference] = useState<Theme | null>(savedTheme);
  const theme: Theme = preference || DEFAULT_THEME;
  useEffect(() => {
    const sync = (event: StorageEvent) => {if (event.key === STORAGE_KEY || event.key === null) setPreference(savedTheme());};
    window.addEventListener('storage', sync);
    return () => {window.removeEventListener('storage', sync);};
  }, []);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#111916' : '#f6f7f7');
  }, [theme]);
  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark';
    setPreference(next);
    try {localStorage.setItem(STORAGE_KEY, next);} catch {/* The current tab still supports switching when storage is unavailable. */}
  }
  return {theme, toggleTheme};
}
