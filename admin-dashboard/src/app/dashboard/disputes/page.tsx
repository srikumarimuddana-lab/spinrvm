"use client";

import { useEffect, useState, useCallback } from "react";
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
import {
  AlertTriangle, RefreshCw, CheckCircle, XCircle, Clock, DollarSign,
  MessageSquare, User, Car,
} from "lucide-react";
import { formatDate, formatCurrency } from "@/lib/utils";
import { getDisputes, resolveDispute } from "@/lib/api";

const STATUS_COLORS: Record<string, string> = {
  open: "bg-red-100 text-red-700",
  under_review: "bg-amber-100 text-amber-700",
  resolved: "bg-green-100 text-green-700",
  rejected: "bg-gray-100 text-gray-500",
};

const REASON_LABELS: Record<string, string> = {
  overcharged: "Overcharged",
  wrong_route: "Wrong Route",
  driver_issue: "Driver Issue",
  payment_error: "Payment Error",
  other: "Other",
};

export default function DisputesPage() {
  const [disputes, setDisputes] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("all");
  const [selected, setSelected] = useState<any>(null);
  const [resolving, setResolving] = useState(false);
  const [resolution, setResolution] = useState("approved");
  const [refundAmount, setRefundAmount] = useState("");
  const [adminNote, setAdminNote] = useState("");

  const fetchDisputes = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getDisputes();
      setDisputes(Array.isArray(data) ? data : []);
    } catch {
      setDisputes([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchDisputes(); }, [fetchDisputes]);

  const filtered = statusFilter === "all"
    ? disputes
    : disputes.filter(d => d.status === statusFilter);

  const handleResolve = async () => {
    if (!selected) return;
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
      fetchDisputes();
    } catch (err) {
      console.error("Failed to resolve dispute:", err);
    } finally {
      setResolving(false);
    }
  };

  // Stats
  const openCount = disputes.filter(d => d.status === "open").length;
  const reviewCount = disputes.filter(d => d.status === "under_review").length;
  const resolvedCount = disputes.filter(d => d.status === "resolved").length;
  const totalRefunded = disputes
    .filter(d => d.status === "resolved")
    .reduce((s, d) => s + Number(d.refund_amount || 0), 0);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <AlertTriangle className="h-6 w-6 text-amber-500" />
            Dispute Resolution
          </h1>
          <p className="text-muted-foreground mt-1">
            Review and resolve rider payment disputes and refund requests
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={fetchDisputes} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-4 gap-4">
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><XCircle className="h-4 w-4 text-red-500" /> Open</div>
          <div className="text-2xl font-bold text-red-600">{openCount}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-amber-500" /> Under Review</div>
          <div className="text-2xl font-bold text-amber-600">{reviewCount}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-green-500" /> Resolved</div>
          <div className="text-2xl font-bold text-green-600">{resolvedCount}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><DollarSign className="h-4 w-4" /> Total Refunded</div>
          <div className="text-2xl font-bold">${totalRefunded.toFixed(2)}</div>
        </CardContent></Card>
      </div>

      {/* Disputes Table */}
      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>All Disputes ({filtered.length})</CardTitle>
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
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Rider</TableHead>
                  <TableHead>Reason</TableHead>
                  <TableHead>Fare</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Filed</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filtered.length === 0 ? (
                  <TableRow><TableCell colSpan={7} className="text-center py-8 text-muted-foreground">No disputes found</TableCell></TableRow>
                ) : filtered.map((d: any) => (
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
                    <TableCell className="font-mono text-red-600">${Number(d.requested_amount || 0).toFixed(2)}</TableCell>
                    <TableCell>
                      <Badge className={STATUS_COLORS[d.status] || "bg-gray-100"}>{d.status}</Badge>
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
          )}
        </CardContent>
      </Card>

      {/* Resolve Dialog */}
      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) setSelected(null); }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-500" />
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
                  <span className="font-mono text-red-600">${Number(selected.requested_amount || 0).toFixed(2)}</span>
                </div>
              </div>

              <div className="bg-muted/30 rounded-lg p-3">
                <p className="text-sm"><strong>Description:</strong> {selected.description}</p>
              </div>

              {(selected.status === "open" || selected.status === "under_review") ? (
                <>
                  <div>
                    <Label>Resolution</Label>
                    <Select value={resolution} onValueChange={setResolution}>
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
                  <div className="flex gap-2">
                    <Button variant="outline" className="flex-1" onClick={() => setSelected(null)}>Cancel</Button>
                    <Button className="flex-1" onClick={handleResolve} disabled={resolving}>
                      {resolving ? "Processing..." : "Submit Resolution"}
                    </Button>
                  </div>
                </>
              ) : (
                <div className="bg-green-50 dark:bg-green-950/30 rounded-lg p-3 space-y-1">
                  <p className="text-sm font-medium text-green-700 dark:text-green-400">Resolution: {selected.resolution}</p>
                  {selected.refund_amount > 0 && <p className="text-sm">Refunded: ${Number(selected.refund_amount).toFixed(2)}</p>}
                  {selected.admin_note && <p className="text-xs text-muted-foreground">Note: {selected.admin_note}</p>}
                  <p className="text-xs text-muted-foreground">Resolved: {formatDate(selected.resolved_at)}</p>
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
