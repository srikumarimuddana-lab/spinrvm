/**
 * Parsing for the heatmap v2 driver allowlist textarea.
 *
 * Extracted because the screen had two different parsers for one field: the
 * save path split on `/[\s,]+/` while the "N driver ID(s) in allowlist" count
 * below it split on `/[\n,]+/`. A space-separated paste therefore displayed as
 * 1 ID and saved as several — the count, which is the only feedback the
 * operator gets before saving, disagreed with what was actually stored.
 *
 * The allowlist decides which drivers get the v2 heatmap during a dark launch,
 * so a silently-wrong entry is not cosmetic: the operator believes a cohort is
 * enrolled and it isn't, and the rollout looks like it did nothing.
 */

/** Canonical UUID v1-v5 shape, which is what `users.id` is. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

export interface ParsedAllowlist {
  /** Every non-empty token, in input order, deduplicated. */
  ids: string[];
  /** Tokens that do not look like a UUID — surfaced, never silently dropped. */
  invalid: string[];
}

/**
 * Split on any whitespace or comma.
 *
 * Deliberately permissive about separators and strict about reporting: an
 * operator pasting from a spreadsheet, a chat message or a CSV should not have
 * to think about which one this field wants.
 */
export function parseAllowlistIds(raw: string): ParsedAllowlist {
  const tokens = raw
    .split(/[\s,]+/)
    .map((s) => s.trim())
    .filter(Boolean);

  const ids: string[] = [];
  const seen = new Set<string>();
  for (const t of tokens) {
    // Dedupe case-insensitively — a UUID pasted twice in different cases is
    // one driver, and sending it twice makes the saved count misleading.
    const key = t.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    ids.push(t);
  }

  return { ids, invalid: ids.filter((t) => !UUID_RE.test(t)) };
}

export function isLikelyUuid(value: string): boolean {
  return UUID_RE.test(value.trim());
}
