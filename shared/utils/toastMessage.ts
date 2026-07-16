/**
 * Character caps for toast content, enforced at the toast entry points
 * (rider store/toastStore.ts::show, driver hooks/useToast.ts::showToast) so
 * no call site can overflow the banner — the render components additionally
 * cap layout via numberOfLines (1 title line, 2 message lines).
 *
 * Kept dependency-free so the toast stores/hooks don't pull the API client
 * (fetch/SecureStore) into their module graph or the Jest sandbox.
 * shared/api/client.ts re-exports these for its existing import sites.
 */

// Toasts render at most two message lines; 140 chars fits that box across
// supported screen widths.
export const TOAST_MESSAGE_MAX = 140;

// Titles render on a single line and should be a short label; anything
// longer is a message masquerading as a title.
export const TOAST_TITLE_MAX = 60;

/**
 * Clamp a toast string to `maxLength`, collapsing whitespace and cutting at a
 * word boundary with an ellipsis rather than truncating mid-word.
 */
export function clampToastMessage(message: string, maxLength = TOAST_MESSAGE_MAX): string {
  const trimmed = message.trim().replace(/\s+/g, ' ');
  if (trimmed.length <= maxLength) return trimmed;
  const slice = trimmed.slice(0, maxLength - 1);
  const lastSpace = slice.lastIndexOf(' ');
  // Prefer a word boundary if one exists reasonably close to the end.
  const cut = lastSpace > maxLength * 0.6 ? slice.slice(0, lastSpace) : slice;
  return `${cut.replace(/[\s.,;:!-]+$/, '')}…`;
}
