"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/page-header";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { AlertTriangle, RefreshCw, Receipt, DollarSign, ExternalLink } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getUnpaidRides, sendPayableRideInvoice, type UnpaidRide } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";

const PAGE_SIZE = 50;

/**
 * Completed rides whose fare was never collected.
 *
 * The backend endpoint (GET /api/admin/rides/unpaid) has existed for a while
 * with no UI, so these rides reached an admin only as a transient push or a
 * live-dashboard alert — miss it and the ride was effectively invisible. This
 * is the queue that was missing.
 *
 * What this screen is NOT: a place to withhold driver money. The driver is
 * already paid for these trips and Spinr absorbs the failed charge — that is
 * deliberate policy (see docs/change-log/2026-09-21-uncollected-rides-stay-
 * payable.md), and the same posture Uber and Lyft take. What is outstanding is
 * a receivable against the RIDER, which is what the invoice action chases.
 */
export default function UnpaidRidesPage() {
  const { allowed } = useRequireModule("rides");
  const [rides, setRides] = useState<UnpaidRide[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  // Per-ride in-flight state, so one slow invoice doesn't disable every button.
  const [sending, setSending] = useState<Record<string, boolean>>({});
  const [sent, setSent] = useState<Record<string, string>>({});
  const reqIdRef = useRef(0);

  const fetchRides = useCallback(async () => {
    const reqId = ++reqIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await getUnpaidRides({ limit: PAGE_SIZE, offset: page * PAGE_SIZE });
      // Drop a response from a superseded request — paging fast would
      // otherwise let an older page land last and overwrite the newer one.
      if (reqId !== reqIdRef.current) return;
      const rows = data?.rides ?? [];
      setRides(rows);
      setHasNextPage(rows.length === PAGE_SIZE);
    } catch (err: unknown) {
      if (reqId !== reqIdRef.current) return;
      const message = err instanceof Error ? err.message : null;
      setError(message || "Could not load unpaid rides. Please try again.");
      setRides([]);
      setHasNextPage(false);
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  }, [page]);

  useEffect(() => {
    if (allowed) fetchRides();
  }, [allowed, fetchRides]);

  const handleSendInvoice = async (rideId: string) => {
    setSending(prev => ({ ...prev, [rideId]: true }));
    setError(null);
    try {
      const res = await sendPayableRideInvoice(rideId);
      // Keep the hosted-invoice URL so the admin can open exactly what the
      // rider received, rather than trusting that the email went out.
      setSent(prev => ({ ...prev, [rideId]: res?.invoice_url || "sent" }));
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : null;
      setError(message || "Could not send the invoice. Please try again.");
    } finally {
      setSending(prev => ({ ...prev, [rideId]: false }));
    }
  };

  const { sorted: sortedRides, sort, toggle } = useTableSort(rides);

  // Page total only — the endpoint returns one page, so summing every page
  // would need an aggregate the backend does not expose. Labelled as such
  // rather than presented as an outstanding-balance figure it isn't.
  const pageOutstanding = rides.reduce(
    (sum, r) => sum + Number(r.total_fare || 0) + Number(r.tip_amount || 0),
    0,
  );

  if (!allowed) return null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            <AlertTriangle className="h-6 w-6 text-warning" />
            Unpaid Rides
          </span>
        }
        description="Completed rides whose fare was never collected. The driver has already been paid — these are outstanding from the rider."
        actions={
          <Button variant="outline" size="sm" onClick={fetchRides} disabled={loading}>
            <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        }
      />

      {error && (
        <Card className="border-destructive/40">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-2 gap-4 sm:max-w-md">
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Receipt className="h-4 w-4 text-warning" /> On this page
          </div>
          <div className="text-2xl font-bold">{loading ? "—" : rides.length}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <DollarSign className="h-4 w-4" /> Outstanding (this page)
          </div>
          <div className="text-2xl font-bold">{loading ? "—" : `$${pageOutstanding.toFixed(2)}`}</div>
        </CardContent></Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Awaiting payment</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex justify-center p-12">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : rides.length === 0 ? (
            <div className="p-12 text-center text-sm text-muted-foreground">
              No unpaid rides. Every completed fare has been collected or waived.
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHead column="rider_name" sort={sort} onSort={toggle}>Rider</SortableHead>
                    <SortableHead column="driver_name" sort={sort} onSort={toggle}>Driver</SortableHead>
                    <SortableHead column="total_fare" sort={sort} onSort={toggle}>Fare</SortableHead>
                    <SortableHead column="payment_retry_count" sort={sort} onSort={toggle}>Retries</SortableHead>
                    <SortableHead column="ride_completed_at" sort={sort} onSort={toggle}>Completed</SortableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedRides.map(ride => {
                    const invoiceUrl = sent[ride.id];
                    return (
                      <TableRow key={ride.id}>
                        <TableCell>
                          <div className="font-medium">{ride.rider_name || "—"}</div>
                          <div className="text-xs text-muted-foreground">{ride.rider_phone || "No phone on file"}</div>
                        </TableCell>
                        <TableCell className="text-sm">{ride.driver_name || "—"}</TableCell>
                        <TableCell className="font-medium">
                          ${(Number(ride.total_fare || 0) + Number(ride.tip_amount || 0)).toFixed(2)}
                        </TableCell>
                        <TableCell>
                          <Badge variant="outline-warning">{ride.payment_retry_count ?? 0}</Badge>
                        </TableCell>
                        <TableCell className="text-sm text-muted-foreground">
                          {ride.ride_completed_at ? formatDate(ride.ride_completed_at) : "—"}
                        </TableCell>
                        <TableCell>
                          {invoiceUrl ? (
                            invoiceUrl === "sent" ? (
                              <span className="text-xs text-success">Invoice sent</span>
                            ) : (
                              <a
                                href={invoiceUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="inline-flex items-center gap-1 text-xs text-success hover:underline"
                              >
                                Invoice sent <ExternalLink className="h-3 w-3" />
                              </a>
                            )
                          ) : (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleSendInvoice(ride.id)}
                              disabled={!!sending[ride.id]}
                            >
                              {sending[ride.id] ? "Sending…" : "Send invoice"}
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
              <Pagination
                page={page}
                onPageChange={setPage}
                hasNextPage={hasNextPage}
                pageSize={PAGE_SIZE}
              />
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
