import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { projectPath } from './api';
import type { Artifact } from './types';

type Props = {
  projectId: string;
  content: string;
  file: Artifact;
  files: Artifact[];
  onSelect: (file: Artifact) => void;
};

type ResolvedLink =
  | {kind: 'external'; url: string}
  | {kind: 'anchor'; fragment: string}
  | {kind: 'artifact'; file: Artifact}
  | {kind: 'missing'; path: string}
  | {kind: 'unsafe'};

/** Resolve only within the supplied task or rollout package, never the host URL. */
function resolveLink(value: string | undefined, current: Artifact, files: Map<string, Artifact>): ResolvedLink {
  if (!value) return {kind: 'unsafe'};
  const url = value.trim();
  if (!url || /[\u0000-\u001f\u007f\\]/.test(url) || url.startsWith('//')) return {kind: 'unsafe'};
  if (/^https?:\/\//i.test(url)) {
    try {
      const parsed = new URL(url);
      if (parsed.username || parsed.password || !parsed.hostname) return {kind: 'unsafe'};
      return {kind: 'external', url: parsed.href};
    } catch {return {kind: 'unsafe'};}
  }
  // Disallow all other schemes, including javascript:, data:, blob: and file:.
  if (/^[a-z][a-z\d+.-]*:/i.test(url)) return {kind: 'unsafe'};
  if (url.startsWith('#')) {
    try {return {kind: 'anchor', fragment: decodeURIComponent(url.slice(1))};}
    catch {return {kind: 'unsafe'};}
  }
  let path: string;
  try {path = decodeURIComponent(url.split(/[?#]/, 1)[0]);}
  catch {return {kind: 'unsafe'};}
  if (/[\u0000-\u001f\u007f\\]/.test(path) || path.startsWith('//') || /^[a-z][a-z\d+.-]*:/i.test(path)) return {kind: 'unsafe'};
  const parts = path.startsWith('/') ? [] : current.path.split('/').slice(0, -1);
  for (const part of path.split('/')) {
    if (!part || part === '.') continue;
    if (part === '..') {
      if (!parts.length) return {kind: 'unsafe'};
      parts.pop();
    } else parts.push(part);
  }
  const resolvedPath = parts.join('/');
  const target = files.get(resolvedPath);
  return target ? {kind: 'artifact', file: target} : {kind: 'missing', path: resolvedPath};
}

function headingSlug(text: string) {
  return text.trim().toLowerCase().replace(/[^\p{L}\p{N}\s_-]/gu, '').replace(/\s/g, '-') || 'section';
}

function textOf(node: unknown): string {
  if (!node || typeof node !== 'object') return '';
  const value = node as {type?: string; value?: unknown; alt?: unknown; children?: unknown[]; properties?: {alt?: unknown}};
  if (value.type === 'html') return '';
  if (typeof value.value === 'string') return value.value;
  if (typeof value.alt === 'string') return value.alt;
  if (typeof value.properties?.alt === 'string') return value.properties.alt;
  return Array.isArray(value.children) ? value.children.map(textOf).join('') : '';
}

function Unavailable({children, reason}: {children?: ReactNode; reason: string}) {
  return <span className="artifact-unavailable" title={reason}>{children}<small>（{reason}）</small></span>;
}

function MarkdownImage({src, alt, title}: {src: string; alt: string; title?: string}) {
  const [failed, setFailed] = useState(false);
  if (failed) return <Unavailable reason="图片无法加载">{alt || '图片'}</Unavailable>;
  return <img src={src} alt={alt} title={title} loading="lazy" onError={() => setFailed(true)} />;
}

export function ArtifactMarkdown({projectId, content, file, files, onSelect}: Props) {
  const prefix = `artifact-${useId().replace(/[^a-z\d_-]/gi, '')}-`;
  const byPath = new Map(files.map(artifact => [artifact.path, artifact]));
  // Set IDs in the syntax tree so React StrictMode cannot increment heading
  // counters by rendering a heading component more than once.
  function headingIdentifiers() {
    return (tree: unknown) => {
      const counts = new Map<string, number>();
      function visit(value: unknown) {
        if (!value || typeof value !== 'object') return;
        const node = value as {type?: string; children?: unknown[]; data?: {hProperties?: Record<string, unknown>}};
        if (node.type === 'heading') {
          const slug = headingSlug(textOf(node));
          const occurrence = counts.get(slug) || 0;
          counts.set(slug, occurrence + 1);
          node.data = {...node.data, hProperties: {...node.data?.hProperties, id: `${prefix}${slug}${occurrence ? `-${occurrence}` : ''}`}};
        }
        node.children?.forEach(visit);
      }
      visit(tree);
    };
  }
  const components: Components = {
    a({href, children, title}) {
      const target = resolveLink(href, file, byPath);
      if (target.kind === 'external') return <a href={target.url} title={title} target="_blank" rel="noopener noreferrer">{children}</a>;
      if (target.kind === 'anchor') {
        const fragment = target.fragment.startsWith('user-content-fn') ? target.fragment : `${prefix}${headingSlug(target.fragment)}`;
        return <a href={`#${fragment}`} title={title}>{children}</a>;
      }
      if (target.kind === 'artifact') return <a href={projectPath(`/api/files/${encodeURIComponent(target.file.id)}/download`, projectId)} title={title || target.file.path} onClick={event => {event.preventDefault(); onSelect(target.file);}}>{children}</a>;
      return <Unavailable reason={target.kind === 'missing' ? `文件未随任务上传：${target.path}` : '链接地址不受支持'}>{children}</Unavailable>;
    },
    img({src, alt, title}) {
      const target = resolveLink(typeof src === 'string' ? src : undefined, file, byPath);
      if (target.kind === 'external') return <MarkdownImage key={target.url} src={target.url} alt={alt || ''} title={title} />;
      if (target.kind === 'artifact') {
        // SVG and HTML are deliberately kept out of inline previews.
        if (!target.file.mime_type.startsWith('image/') || /svg/i.test(target.file.mime_type)) return <Unavailable reason="此文件不支持图片预览">{alt || target.file.path}</Unavailable>;
        return <MarkdownImage key={`${projectId}:${target.file.id}`} src={projectPath(`/api/files/${encodeURIComponent(target.file.id)}/download`, projectId)} alt={alt || target.file.path} title={title} />;
      }
      return <Unavailable reason={target.kind === 'missing' ? `图片未随任务上传：${target.path}` : '图片地址不受支持'}>{alt || '图片'}</Unavailable>;
    },
  };
  // All href/src values are handled by the resolver above; raw HTML stays disabled.
  return <ReactMarkdown remarkPlugins={[remarkGfm, headingIdentifiers]} components={components} urlTransform={url => url}>{content}</ReactMarkdown>;
}

export default ArtifactMarkdown;
