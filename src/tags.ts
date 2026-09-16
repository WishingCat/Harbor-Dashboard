// Subject tags offered in the upload form and in the library filter. Uploads may
// also carry any other tag, so treat this as a starting point, not a whitelist.
// Keep in sync with PRESET_TAGS in server/storage.py.
export const PRESET_TAGS = ['物理', '化学', '生物', '医学', '人工智能', '具身智能', '编程'];

export const MAX_TAGS = 20;
export const MAX_TAG_LENGTH = 50;

/** Trim, drop blanks and duplicates, and keep the order the user chose them in. */
export function normalizeTags(values: string[]): string[] {
  const seen: string[] = [];
  for (const value of values) {
    const tag = value.trim();
    if (tag && tag.length <= MAX_TAG_LENGTH && !seen.includes(tag)) seen.push(tag);
  }
  return seen.slice(0, MAX_TAGS);
}

/** Presets first in their documented order, then custom tags alphabetically. */
export function orderTags(values: Iterable<string>): string[] {
  const unique = [...new Set(values)];
  return [...PRESET_TAGS.filter(tag => unique.includes(tag)),
          ...unique.filter(tag => !PRESET_TAGS.includes(tag)).sort((a, b) => a.localeCompare(b, 'zh-Hans-CN'))];
}
