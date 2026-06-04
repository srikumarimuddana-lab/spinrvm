"use client";

import { useEffect, useState, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { CheckCircle, XCircle, Clock, RefreshCw, Send } from "lucide-react";
import { getDriverOfferStats } from "@/lib/api";

const DATE_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "90d", label: "90 Days" },
  { value: "1y", label: "1 Year" },
];

type SortKey = "offered" | "accepted" | "declined" | "ignored" | "preempted" | "accept_rate" | "ignore_rate";

export default function DriverOffersPage() {
  const [dateRange, setDateRange] = useState("30d");
  const [loading, setLoading] = useState(true);
  const [data, setData] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("offered");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // Don't swallow the error into null — a 403/503/network failure must
      // read as an error, not a false "No offers in this window".
      setData(await getDriverOfferStats(dateRange));
    } catch (e: any) {
      setData(null);
      setError(e?.message || "Failed to load offer analytics. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [dateRange]);

  useEffect(() => { fetchData(); }, [fetchData]);

  const totals = data?.totals || {};
  const drivers: any[] = Array.isArray(data?.drivers) ? [...data.drivers] : [];
  drivers.sort((a, b) => (b[sortKey] ?? 0) - (a[sortKey] ?? 0));

  const kpis = [
    { label: "Offers Sent", value: totals.offered ?? 0, Icon: Send, cls: "text-foreground" },
    { label: "Accepted", value: totals.accepted ?? 0, Icon: CheckCircle, cls: "text-emerald-600" },
    { label: "Declined", value: totals.declined ?? 0, Icon: XCircle, cls: "text-red-600" },
    { label: "Ignored", value: totals.ignored ?? 0, Icon: Clock, cls: "text-amber-600" },
  ];

  const SortHead = ({ k, children }: { k: SortKey; children: React.ReactNode }) => (
    <TableHead
      onClick={() => setSortKey(k)}
      className={`cursor-pointer select-none text-right ${sortKey === k ? "text-primary font-bold" : ""}`}
    >
      {children}{sortKey === k ? " ↓" : ""}
    </TableHead>
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Driver Offer Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Dispatch funnel per driver — who accepts, declines, and ignores ride offers.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select value={dateRange} onValueChange={setDateRange}>
            <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
            <SelectContent>
              {DATE_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            onClick={fetchData}
            className="flex items-center gap-1.5 text-sm border rounded-lg px-3 py-2 hover:bg-muted transition-colors"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {kpis.map(({ label, value, Icon, cls }) => (
          <Card key={label}>
            <CardContent className="pt-4">
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Icon className={`h-4 w-4 ${cls}`} /> {label}
              </div>
              <div className={`text-2xl font-bold mt-1 tabular-nums ${cls}`}>{Number(value).toLocaleString()}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Per-Driver Breakdown {data?.total_drivers ? `(${data.total_drivers})` : ""}</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-16 text-center text-sm text-muted-foreground">Loading…</div>
          ) : error ? (
            <div className="py-16 text-center space-y-3">
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
              <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
          ) : drivers.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">No offers in this window.</div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Driver</TableHead>
                  <SortHead k="offered">Offered</SortHead>
                  <SortHead k="accepted">Accepted</SortHead>
                  <SortHead k="declined">Declined</SortHead>
                  <SortHead k="ignored">Ignored</SortHead>
                  <SortHead k="preempted">Preempted</SortHead>
                  <SortHead k="accept_rate">Accept %</SortHead>
                  <SortHead k="ignore_rate">Ignore %</SortHead>
                  <TableHead className="text-right">Avg Reply</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {drivers.map((d) => (
                  <TableRow key={d.driver_id}>
                    <TableCell className="font-medium">
                      <span className="flex items-center gap-2">
                        {d.is_online && <span className="h-2 w-2 rounded-full bg-emerald-500" title="Online" />}
                        {d.name}
                      </span>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{d.offered}</TableCell>
                    <TableCell className="text-right tabular-nums text-emerald-600">{d.accepted}</TableCell>
                    <TableCell className="text-right tabular-nums text-red-600">{d.declined}</TableCell>
                    <TableCell className="text-right tabular-nums text-amber-600">{d.ignored}</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">{d.preempted ?? 0}</TableCell>
                    <TableCell className="text-right tabular-nums font-semibold">{d.accept_rate}%</TableCell>
                    <TableCell className="text-right tabular-nums">{d.ignore_rate}%</TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {d.avg_response_seconds != null ? `${d.avg_response_seconds}s` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
