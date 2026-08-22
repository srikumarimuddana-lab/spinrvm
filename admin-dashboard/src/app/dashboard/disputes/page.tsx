"use client";

import { useEffect, useRef, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Pagination } from "@/components/ui/pagination";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  AlertTriangle, RefreshCw, CheckCircle, XCircle, Clock, DollarSign,
  User,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getDisputes, getDisputeStats, resolveDispute } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import ChargebacksTab from "./chargebacks-tab";

const STATUS_COLORS: Record<string, string> = {
  open: "bg-destructive/15 text-destructive",
  under_review: "bg-warning/15 text-warning",
  resolved: "bg-success/15 text-success",
  rejected: "bg-muted text-muted-foreground",
};

const REASON_LABELS: Record<string, string> = {
  overcharged: "Overcharged",
  wrong_route: "Wrong Route",
  driver_issue: "Driver Issue",
  payment_error: "Payment Error",
  other: "Other",
};

const PAGE_SIZE = 50;

interface DisputeStats {
  open: number;
  under_review: number;
  resolved: number;
  rejected: number;
  total_refunded: number;
}

export default function DisputesPage() {
  const { allowed } = useRequireModule("support");
  const [disputes, setDisputes] = useState<any[]>([]);
  const [stats, setStats] = useState<DisputeStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [hasNextPage, setHasNextPage] = useState(false);
  const reqIdRef = useRef(0);

  const [selected, setSelected] = useState<any>(null);
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState("approved");
  const [refundAmount, setRefundAmount] = useState("");
  const [adminNote, setAdminNote] = useState("");
  const [resolveError, setResolveError] = useState<string | null>(null);

  const fetchDisputes = async () => {
    setLoading(true);
    const reqId = ++reqIdRef.current;
    try {
      const data = await getDisputes({
        limit: PAGE_SIZE + 1,
        offset: page * PAGE_SIZE,
        status: statusFilter,
      });
      if (reqId !== reqIdRef.current) return;
      const arr = Array.isArray(data) ? data : [];
      setHasNextPage(arr.length > PAGE_SIZE);
      setDisputes(arr.slice(0, PAGE_SIZE));
    } catch {
      if (reqId !== reqIdRef.current) return;
      setDisputes([]);
      setHasNextPage(false);
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      const data = await getDisputeStats();
      setStats(data ?? null);
    } catch {
      setStats(null);
    }
  };

  // Reset to page 0 on status filter change.
  useEffect(() => {
    if (page !== 0) setPage(0);
    else fetchDisputes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  // Re-fetch on page change.
  useEffect(() => {
    fetchDisputes();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  // Stats once on mount.
  useEffect(() => { fetchStats(); }, []);

  const refresh = () => { fetchDisputes(); fetchStats(); };

  const handleResolve = async () => {
    if (!selected) return;
    setResolveError(null);

    if (resolution === "partial_refund") {
      const amount = parseFloat(refundAmount);
      if (isNaN(amount) || amount <= 0) {
        setResolveError("Refund amount must be greater than zero");
        return;
      }
      const originalFareAmount = Number(selected.original_fare || 0);
      if (amount > originalFareAmount) {
        setResolveError(
          `Refund cannot exceed the original fare of $${originalFareAmount.toFixed(2)}`
        );
        return;
      }
    }

    setResolving(true);
    try {
      await resolveDispute(selected.id, {
        resolution,
        refund_amount: refundAmount ? Number(refundAmount) : undefined,
        admin_note: adminNote || undefined,
      });
      setSelected(null);
      setResolution("approved");
      setRefundAmount("");
      setAdminNote("");
      setResolveError(null);
      refresh();
    } catch (err) {
      console.error("Failed to resolve dispute:", err);
    } finally {
      setResolving(false);
    }
  };

  const { sorted: sortedDisputes, sort, toggle } = useTableSort(disputes);

  if (!allowed) return null;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <AlertTriangle className="h-6 w-6 text-warning" />
            Dispute Resolution
          </h1>
          <p className="text-muted-foreground mt-1">
            Review and resolve rider payment disputes and refund requests
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <Tabs defaultValue="rider">
        <TabsList>
          <TabsTrigger value="rider">Rider Disputes</TabsTrigger>
          <TabsTrigger value="chargebacks">Chargebacks</TabsTrigger>
        </TabsList>

        <TabsContent value="rider" className="space-y-6">
      {/* Stats (aggregate, not per-page) */}
      <div className="grid grid-cols-4 gap-4">
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><XCircle className="h-4 w-4 text-destructive" /> Open</div>
          <div className="text-2xl font-bold text-destructive">{stats?.open ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-warning" /> Under Review</div>
          <div className="text-2xl font-bold text-warning">{stats?.under_review ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-success" /> Resolved</div>
          <div className="text-2xl font-bold text-success">{stats?.resolved ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><DollarSign className="h-4 w-4" /> Total Refunded</div>
          <div className="text-2xl font-bold">${(stats?.total_refunded ?? 0).toFixed(2)}</div>
        </CardContent></Card>
      </div>

      {/* Disputes Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Disputes</CardTitle>
          <div className="flex gap-1 bg-muted rounded-lg p-0.5">
            {["all", "open", "under_review", "resolved", "rejected"].map(s => (
              <button key={s} onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"}`}>
                {s === "under_review" ? "Review" : s.charAt(0).toUpperCase() + s.slice(1)}
              </button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {loading ? (
            <div className="flex justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <SortableHead column="user_name" sort={sort} onSort={toggle}>Rider</SortableHead>
                    <SortableHead column="reason" sort={sort} onSort={toggle}>Reason</SortableHead>
                    <SortableHead column="original_fare" sort={sort} onSort={toggle}>Fare</SortableHead>
                    <SortableHead column="requested_amount" sort={sort} onSort={toggle}>Requested</SortableHead>
                    <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                    <SortableHead column="created_at" sort={sort} onSort={toggle}>Filed</SortableHead>
                    <TableHead>Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedDisputes.length === 0 ? (
                    <TableRow><TableCell colSpan={7} className="text-center py-8 text-muted-foreground">No disputes found</TableCell></TableRow>
                  ) : sortedDisputes.map((d: any) => (
                    <TableRow key={d.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(d)}>
                      <TableCell>
                        <div className="flex items-center gap-2">
                          <User className="h-4 w-4 text-muted-foreground" />
                          <div>
                            <p className="font-medium text-sm">{d.user_name || "Unknown"}</p>
                            <p className="text-xs text-muted-foreground">{d.user_phone || ""}</p>
                          </div>
                        </div>
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline">{REASON_LABELS[d.reason] || d.reason}</Badge>
                      </TableCell>
                      <TableCell className="font-mono">${Number(d.original_fare || 0).toFixed(2)}</TableCell>
                      <TableCell className="font-mono text-destructive">${Number(d.requested_amount || 0).toFixed(2)}</TableCell>
                      <TableCell>
                        <Badge className={STATUS_COLORS[d.status] || "bg-muted"}>{d.status}</Badge>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{formatDate(d.created_at)}</TableCell>
                      <TableCell>
                        {d.status === "open" || d.status === "under_review" ? (
                          <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); setSelected(d); }}>
                            Resolve
                          </Button>
                        ) : (
                          <span className="text-xs text-muted-foreground">{d.resolution || "—"}</span>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <div className="px-4 border-t">
                <Pagination
                  page={page}
                  pageSize={PAGE_SIZE}
                  hasNextPage={hasNextPage}
                  onPageChange={setPage}
                />
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Resolve Dialog */}
      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-warning" />
              Resolve Dispute
            </DialogTitle>
          </DialogHeader>
          {selected && (
            <div className="space-y-4">
              <div className="bg-muted/50 rounded-lg p-3 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Rider</span>
                  <span className="font-medium">{selected.user_name}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Reason</span>
                  <Badge variant="outline">{REASON_LABELS[selected.reason] || selected.reason}</Badge>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Original Fare</span>
                  <span className="font-mono">${Number(selected.original_fare || 0).toFixed(2)}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Requested Refund</span>
                  <span className="font-mono text-destructive">${Number(selected.requested_amount || 0).toFixed(2)}</span>
                </div>
              </div>

              <div className="bg-muted/30 rounded-lg p-3">
                <p className="text-sm"><strong>Description:</strong> {selected.description}</p>
              </div>

              {(selected.status === "open" || selected.status === "under_review") ? (
                <>
                  <div>
                    <Label>Resolution</Label>
                    <Select value={resolution} onValueChange={(v) => { setResolution(v); setResolveError(null); }}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="approved">Approve Full Refund</SelectItem>
                        <SelectItem value="partial_refund">Partial Refund</SelectItem>
                        <SelectItem value="rejected">Reject</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  {resolution === "partial_refund" && (
                    <div>
                      <Label>Refund Amount ($)</Label>
                      <Input type="number" step="0.01" value={refundAmount} onChange={e => setRefundAmount(e.target.value)} placeholder={String(selected.requested_amount || 0)} />
                    </div>
                  )}
                  <div>
                    <Label>Admin Note (optional)</Label>
                    <Input value={adminNote} onChange={e => setAdminNote(e.target.value)} placeholder="Internal note about this resolution" />
                  </div>
                  {resolveError && (
                    <p className="text-sm text-destructive">{resolveError}</p>
                  )}
                  <div className="flex gap-2">
                    <Button variant="outline" className="flex-1" onClick={() => { setSelected(null); setResolveError(null); }}>Cancel</Button>
                    <Button className="flex-1" onClick={handleResolve} disabled={resolving}>
                      {resolving ? "Processing..." : "Submit Resolution"}
                    </Button>
                  </div>
                </>
              ) : (
                <div className="bg-success/10 rounded-lg p-3 space-y-1">
                  <p className="text-sm font-medium text-success">Resolution: {selected.resolution}</p>
                  {selected.refund_amount > 0 && <p className="text-sm">Refunded: ${Number(selected.refund_amount).toFixed(2)}</p>}
                  {selected.admin_note && <p className="text-xs text-muted-foreground">Note: {selected.admin_note}</p>}
                  <p className="text-xs text-muted-foreground">Resolved: {formatDate(selected.resolved_at)}</p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
        </TabsContent>

        <TabsContent value="chargebacks">
          <ChargebacksTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
