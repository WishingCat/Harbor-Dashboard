/**
 * Copy text, including on plain-HTTP origins.
 *
 * navigator.clipboard only exists in a secure context, so on a deployment reached
 * as http://host:port it is undefined and the modern call cannot even be tried.
 * The deprecated execCommand path still works there, which is the difference
 * between a copy button that works and one that appears to do nothing.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {/* Fall through: a denied permission still leaves execCommand worth trying. */}
  return legacyCopy(text);
}

function legacyCopy(text: string): boolean {
  const area = document.createElement('textarea');
  area.value = text;
  area.setAttribute('readonly', '');
  // Keep it off-screen but focusable; display:none would make the selection fail.
  area.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;';
  document.body.appendChild(area);
  const selection = document.getSelection();
  const previous = selection && selection.rangeCount ? selection.getRangeAt(0) : null;
  let copied = false;
  try {
    area.select();
    area.setSelectionRange(0, text.length);
    copied = document.execCommand('copy');
  } catch {copied = false;}
  area.remove();
  // Restore whatever the reader had selected before pressing the button.
  if (previous && selection) {selection.removeAllRanges(); selection.addRange(previous);}
  return copied;
}
