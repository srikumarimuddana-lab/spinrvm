"use client";

// Card-network chargebacks (Stripe disputes) — C23 Action item 3. Distinct
// from the rider-raised disputes tab: reads `stripe_disputes` via
// GET /api/admin/disputes/chargebacks. Read-only for now — chargebacks are
// still resolved via the Stripe Dashboard
// (docs/runbooks/payment-dispute-evidence.md); this tab exists so an admin
// can see an open chargeback's deadline without a raw SQL query.

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getChargebacks, type Chargeback } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  needs_response: "bg-red-100 text-red-700",
  warning_needs_response: "bg-red-100 text-red-700",
  under_review: "bg-amber-100 text-amber-700",
  warning_under_review: "bg-amber-100 text-amber-700",
  won: "bg-green-100 text-green-700",
  lost: "bg-gray-100 text-gray-500",
  warning_closed: "bg-green-100 text-green-700",
};

const OPEN_STATUSES = new Set(["needs_response", "warning_needs_response", "under_review", "warning_under_review"]);

const PAGE_SIZE = 50;

function daysRemainingLabel(days: number | null, status: string): string {
  if (!OPEN_STATUSES.has(status)) return "—";
  if (days == null) return "—";
  if (days < 0) return "Overdue";
  if (days === 0) return "Due today";
  return `${days} day${days === 1 ? "" : "s"}`;
}

function daysRemainingColor(days: number | null, status: string): string {
  if (!OPEN_STATUSES.has(status) || days == null) return "text-muted-foreground";
  if (days <= 1) return "text-red-600 font-semibold";
  if (days <= 3) return "text-amber-600 font-medium";
  return "text-muted-foreground";
}

export default function ChargebacksTab() {
  const [chargebacks, setChargebacks] = useState<Chargeback[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  const reqIdRef = useRef(0);

  const fetchChargebacks = async () => {
    setLoading(true);
    const reqId = ++reqIdRef.current;
    try {
      const data = await getChargebacks({
        limit: PAGE_SIZE + 1,
        offset: page * PAGE_SIZE,
        status: statusFilter,
      });
      if (reqId !== reqIdRef.current) return;
      const arr = Array.isArray(data) ? data : [];
      setHasNextPage(arr.length > PAGE_SIZE);
      setChargebacks(arr.slice(0, PAGE_SIZE));
      setError(false);
    } catch (err) {
      if (reqId !== reqIdRef.current) return;
      // Deadline-monitoring surface (C23) — a failed fetch must not render
      // identically to a genuine "no chargebacks" result, or an admin could
      // miss a real evidence deadline. See docs/change-log for the fix.
      console.error("Failed to fetch chargebacks:", err);
      setChargebacks([]);
      setHasNextPage(false);
      setError(true);
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  };

  useEffect(() => {
    if (page !== 0) setPage(0);
    else fetchChargebacks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  useEffect(() => {
    fetchChargebacks();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const { sorted: sortedChargebacks, sort, toggle } = useTableSort(chargebacks);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          Card-network chargebacks from Stripe. Evidence deadlines are alerted 3 days out
          (see runbook for the manual response path) — resolve via the Stripe Dashboard.
        </p>
        <Button variant="outline" size="sm" onClick={fetchChargebacks} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Chargebacks</CardTitle>
          <div className="flex gap-1 bg-muted rounded-lg p-0.5">
            {["all", "needs_response", "under_review", "won", "lost"].map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                aria-pressed={statusFilter === s}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${
                  statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"
                }`}
              >
                {s === "all" ? "All" : s.replace(/_/g, " ")}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex justify-center p-12" role="status" aria-live="polite">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
              <span className="sr-only">Loading chargebacks…</span>
            </div>
          ) : (
            <>
              {error && (
                <div className="flex items-center justify-between gap-3 border-b bg-red-50 dark:bg-red-950/30 px-4 py-3 text-sm text-red-700 dark:text-red-400">
                  <span className="flex items-center gap-2">
                    <AlertTriangle className="h-4 w-4" />
                    Failed to load chargebacks. The list below may be incomplete.
                  </span>
                  <Button variant="outline" size="sm" onClick={fetchChargebacks}>
                    Retry
                  </Button>
                </div>
              )}
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHead column="ride_code" sort={sort} onSort={toggle}>Ride</SortableHead>
                    <SortableHead column="reason" sort={sort} onSort={toggle}>Reason</SortableHead>
                    <SortableHead column="amount_cents" sort={sort} onSort={toggle}>Amount</SortableHead>
                    <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                    <SortableHead column="evidence_due_by" sort={sort} onSort={toggle}>Evidence Due</SortableHead>
                    <TableHead>Days Remaining</TableHead>
                    <SortableHead column="created_at" sort={sort} onSort={toggle}>Filed</SortableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedChargebacks.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={7} className="text-center py-8 text-muted-foreground">
                        No chargebacks found
                      </TableCell>
                    </TableRow>
                  ) : (
                    sortedChargebacks.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell className="font-mono text-sm">{c.ride_code || "—"}</TableCell>
                        <TableCell>
                          <Badge variant="outline">{c.reason?.replace(/_/g, " ") || "unknown"}</Badge>
                        </TableCell>
                        <TableCell className="font-mono">
                          ${(c.amount_cents / 100).toFixed(2)}
                        </TableCell>
                        <TableCell>
                          <Badge className={STATUS_COLORS[c.status] || "bg-gray-100"}>
                            {c.status?.replace(/_/g, " ")}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {c.evidence_due_by ? formatDate(c.evidence_due_by) : "—"}
                        </TableCell>
                        <TableCell className={`text-xs ${daysRemainingColor(c.days_remaining, c.status)}`}>
                          <div className="flex items-center gap-1">
                            {OPEN_STATUSES.has(c.status) && c.days_remaining != null && c.days_remaining <= 3 && (
                              <AlertTriangle className="h-3 w-3" />
                            )}
                            {daysRemainingLabel(c.days_remaining, c.status)}
                          </div>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {formatDate(c.created_at)}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
              <div className="px-4 border-t">
                <Pagination page={page} pageSize={PAGE_SIZE} hasNextPage={hasNextPage} onPageChange={setPage} />
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
