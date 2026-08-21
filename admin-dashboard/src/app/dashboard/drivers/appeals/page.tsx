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
  Gavel, RefreshCw, CheckCircle, XCircle, Clock,
} from "lucide-react";
import { formatDate } from "@/lib/utils";
import { getDriverAppeals, getDriverAppealStats, resolveDriverAppeal, type DriverAppeal, type DriverAppealStats } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-warning/15 text-warning",
  approved: "bg-success/15 text-success",
  denied: "bg-destructive/15 text-destructive",
};

const APPEAL_TYPE_LABELS: Record<string, string> = {
  suspension: "Suspension",
  ban: "Ban",
  needs_review: "Needs Review",
  other: "Other",
};

export default function DriverAppealsPage() {
  const { allowed } = useRequireModule("drivers");
  const [appeals, setAppeals] = useState<DriverAppeal[]>([]);
  const [stats, setStats] = useState<DriverAppealStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState("pending");
  const reqIdRef = useRef(0);

  const [selected, setSelected] = useState<DriverAppeal | null>(null);
  const [resolving, setResolving] = useState(false);
  const [adminNote, setAdminNote] = useState("");
  const [resolveError, setResolveError] = useState<string | null>(null);

  const fetchAppeals = async () => {
    setLoading(true);
    const reqId = ++reqIdRef.current;
    try {
      const data = await getDriverAppeals(statusFilter);
      if (reqId !== reqIdRef.current) return;
      setAppeals(Array.isArray(data) ? data : []);
    } catch {
      if (reqId !== reqIdRef.current) return;
      setAppeals([]);
    } finally {
      if (reqId === reqIdRef.current) setLoading(false);
    }
  };

  const fetchStats = async () => {
    try {
      setStats(await getDriverAppealStats());
    } catch {
      setStats(null);
    }
  };

  useEffect(() => {
    fetchAppeals();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter]);

  useEffect(() => { fetchStats(); }, []);

  const refresh = () => { fetchAppeals(); fetchStats(); };

  const handleResolve = async (decision: "approved" | "denied") => {
    if (!selected) return;
    setResolveError(null);
    setResolving(true);
    try {
      await resolveDriverAppeal(selected.id, { decision, admin_note: adminNote || undefined });
      setSelected(null);
      setAdminNote("");
      refresh();
    } catch (err: any) {
      setResolveError(err?.message || "Failed to resolve appeal. Please try again.");
    } finally {
      setResolving(false);
    }
  };

  if (!allowed) return null;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Gavel className="h-6 w-6 text-amber-500" />
            Driver Appeals
          </h1>
          <p className="text-muted-foreground mt-1">
            Review driver appeals of a suspension, ban, or account hold — see docs/legal/driver-deactivation-appeals-policy.md
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={refresh} disabled={loading}>
          <RefreshCw className={`h-4 w-4 mr-1 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-warning" /> Pending</div>
          <div className="text-2xl font-bold text-warning">{stats?.pending ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-success" /> Approved</div>
          <div className="text-2xl font-bold text-success">{stats?.approved ?? "—"}</div>
        </CardContent></Card>
        <Card><CardContent className="pt-4">
          <div className="flex items-center gap-2 text-sm text-muted-foreground"><XCircle className="h-4 w-4 text-destructive" /> Denied</div>
          <div className="text-2xl font-bold text-destructive">{stats?.denied ?? "—"}</div>
        </CardContent></Card>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Appeals</CardTitle>
          <div className="flex gap-1 bg-muted rounded-lg p-0.5">
            {["all", "pending", "approved", "denied"].map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded-md text-xs font-medium transition ${statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"}`}
              >
                {s.charAt(0).toUpperCase() + s.slice(1)}
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
                  <TableHead>Driver ID</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Message</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Submitted</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {appeals.length === 0 ? (
                  <TableRow><TableCell colSpan={6} className="text-center py-8 text-muted-foreground">No appeals found</TableCell></TableRow>
                ) : appeals.map((a) => (
                  <TableRow key={a.id} className="cursor-pointer hover:bg-muted/50" onClick={() => setSelected(a)}>
                    <TableCell className="font-mono text-xs">{a.driver_id.slice(0, 8)}…</TableCell>
                    <TableCell><Badge variant="outline">{APPEAL_TYPE_LABELS[a.appeal_type] || a.appeal_type}</Badge></TableCell>
                    <TableCell className="max-w-xs truncate text-sm">{a.driver_message}</TableCell>
                    <TableCell><Badge className={STATUS_COLORS[a.status] || "bg-muted"}>{a.status}</Badge></TableCell>
                    <TableCell className="text-xs text-muted-foreground">{formatDate(a.created_at)}</TableCell>
                    <TableCell>
                      {a.status === "pending" ? (
                        <Button size="sm" variant="outline" onClick={(e) => { e.stopPropagation(); setSelected(a); }}>
                          Review
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">{a.resolved_by || "—"}</span>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!selected} onOpenChange={(open) => { if (!open) { setSelected(null); setResolveError(null); } }}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Gavel className="h-5 w-5 text-amber-500" />
              Review Appeal
            </DialogTitle>
          </DialogHeader>
          {selected && (
            <div className="space-y-4">
              <div className="bg-muted/50 rounded-lg p-3 space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Driver</span>
                  <span className="font-mono text-xs">{selected.driver_id}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Appeal type</span>
                  <Badge variant="outline">{APPEAL_TYPE_LABELS[selected.appeal_type] || selected.appeal_type}</Badge>
                </div>
                {selected.original_reason && (
                  <div className="text-sm">
                    <span className="text-muted-foreground">Original reason: </span>
                    {selected.original_reason}
                  </div>
                )}
              </div>

              <div className="bg-muted/30 rounded-lg p-3">
                <p className="text-sm"><strong>Driver's message:</strong> {selected.driver_message}</p>
              </div>

              {selected.status === "pending" ? (
                <>
                  <div>
                    <Label>Admin note (optional, shared internally)</Label>
                    <Input value={adminNote} onChange={(e) => setAdminNote(e.target.value)} placeholder="Reasoning for this decision" />
                  </div>
                  {resolveError && <p className="text-sm text-destructive">{resolveError}</p>}
                  <p className="text-xs text-muted-foreground">
                    Approving a suspension or ban appeal automatically reactivates the driver's account.
                  </p>
                  <div className="flex gap-2">
                    <Button variant="outline" className="flex-1" onClick={() => handleResolve("denied")} disabled={resolving}>
                      {resolving ? "Processing..." : "Deny"}
                    </Button>
                    <Button className="flex-1" onClick={() => handleResolve("approved")} disabled={resolving}>
                      {resolving ? "Processing..." : "Approve & Reactivate"}
                    </Button>
                  </div>
                </>
              ) : (
                <div className={`rounded-lg p-3 space-y-1 ${selected.status === "approved" ? "bg-green-50 dark:bg-green-950/30" : "bg-red-50 dark:bg-red-950/30"}`}>
                  <p className={`text-sm font-medium ${selected.status === "approved" ? "text-green-700 dark:text-green-400" : "text-red-700 dark:text-red-400"}`}>
                    {selected.status === "approved" ? "Approved" : "Denied"}
                  </p>
                  {selected.admin_note && <p className="text-xs text-muted-foreground">Note: {selected.admin_note}</p>}
                  {selected.resolved_at && <p className="text-xs text-muted-foreground">Resolved: {formatDate(selected.resolved_at)}</p>}
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
