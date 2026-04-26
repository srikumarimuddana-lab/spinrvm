"use client";

import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./button";

export interface PaginationProps {
  /** 0-based page index. */
  page: number;
  pageSize: number;
  /** True iff the API returned more than `pageSize` rows on the current page. */
  hasNextPage: boolean;
  /** Optional: total row count if the endpoint exposes it. */
  totalCount?: number;
  onPageChange: (next: number) => void;
  className?: string;
}

/**
 * Pagination control. Designed for the "fetch limit + 1" pattern:
 * the page asks for `pageSize + 1` rows, slices to `pageSize`, and passes
 * `hasNextPage = response.length > pageSize`. No total-count round-trip
 * required.
 */
export function Pagination({
  page,
  pageSize,
  hasNextPage,
  totalCount,
  onPageChange,
  className,
}: PaginationProps) {
  const start = page * pageSize + 1;
  const end = page * pageSize + pageSize;
  const label =
    typeof totalCount === "number"
      ? `${start}–${Math.min(end, totalCount)} of ${totalCount}`
      : `Page ${page + 1}`;

  return (
    <div className={`flex items-center justify-between gap-2 py-3 ${className ?? ""}`}>
      <div className="text-sm text-muted-foreground">{label}</div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(Math.max(0, page - 1))}
          disabled={page === 0}
        >
          <ChevronLeft className="size-4" />
          Prev
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onPageChange(page + 1)}
          disabled={!hasNextPage}
        >
          Next
          <ChevronRight className="size-4" />
        </Button>
      </div>
    </div>
  );
}

export default Pagination;
