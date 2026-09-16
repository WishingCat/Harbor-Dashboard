export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { credentials: 'same-origin', ...options,
    headers: {...(options?.body && !(options.body instanceof FormData) ? {'Content-Type': 'application/json'} : {}), ...options?.headers},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === 'string' ? body.detail : `请求失败 (${response.status})`);
  }
  return response.json();
}
export const post = <T,>(path: string, body: unknown) => api<T>(path, {method: 'POST', body: JSON.stringify(body)});
export function projectPath(path: string, projectId: string) {
  const separator = path.includes('?') ? '&' : '?';
  return `${path}${separator}project_id=${encodeURIComponent(projectId)}`;
}
export function relativeTime(date: string) {
  const seconds = Math.max(0, (Date.now() - new Date(date).getTime()) / 1000);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 604800) return `${Math.floor(seconds / 86400)} 天前`;
  return new Date(date).toLocaleDateString('zh-CN', {month: 'short', day: 'numeric'});
}
export const sizeLabel = (size: number) => size < 1024 ? `${size} B` : size < 1048576 ? `${(size / 1024).toFixed(1)} KB` : `${(size / 1048576).toFixed(1)} MB`;
