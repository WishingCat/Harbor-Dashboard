import { useEffect, useState } from 'react';

type Theme = 'light' | 'dark';
const STORAGE_KEY = 'harbor-theme';

function savedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {return null;}
}

export function useTheme() {
  const [preference, setPreference] = useState<Theme | null>(savedTheme);
  const [systemDark, setSystemDark] = useState(() => matchMedia('(prefers-color-scheme: dark)').matches);
  const theme: Theme = preference || (systemDark ? 'dark' : 'light');
  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const update = () => setSystemDark(media.matches);
    const sync = (event: StorageEvent) => {if (event.key === STORAGE_KEY || event.key === null) setPreference(savedTheme());};
    media.addEventListener('change', update);
    window.addEventListener('storage', sync);
    return () => {media.removeEventListener('change', update); window.removeEventListener('storage', sync);};
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
