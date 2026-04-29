/**
 * Standard envelope for any paginated list response.
 *
 * Cursor-based by default (matches Phase 5 audit recommendation).
 * Use `next_cursor=null` to signal the last page; clients should not
 * paginate further. `total` is optional because counting may be
 * expensive at scale and not all endpoints will populate it.
 */
export interface PaginatedResponse<T> {
  /** Page items in stable order */
  items: T[];
  /** Opaque cursor to pass back as ?cursor=... for the next page; null on last page */
  next_cursor: string | null;
  /** Convenience: true iff next_cursor !== null */
  has_more: boolean;
  /** Optional total — endpoints that can count cheaply may include this */
  total?: number;
}

/** Standard query params clients send. `limit` defaults are per-endpoint. */
export interface PaginationQuery {
  cursor?: string;
  limit?: number;
}
