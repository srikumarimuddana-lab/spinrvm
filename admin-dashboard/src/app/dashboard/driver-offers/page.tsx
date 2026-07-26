"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table, TableBody, TableCell, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend,
} from "recharts";
import {
  CheckCircle, XCircle, Clock, RefreshCw, Send, Search, ChevronLeft, ChevronRight,
  X, BarChart3, MapPin,
} from "lucide-react";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { getDriverOfferStats, getDriverOfferTrends, getServiceAreas } from "@/lib/api";

const DATE_RANGES = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 Days" },
  { value: "30d", label: "30 Days" },
  { value: "90d", label: "90 Days" },
  { value: "1y", label: "1 Year" },
];

const PAGE_SIZE = 15;
const ALL_AREAS = "__all__";

type SortKey =
  | "name" | "offered" | "accepted" | "declined" | "ignored" | "preempted"
  | "accept_rate" | "decline_rate" | "ignore_rate" | "avg_response_seconds";

const COLUMNS: { key: SortKey; label: string; numeric: boolean }[] = [
  { key: "name", label: "Driver", numeric: false },
  { key: "offered", label: "Offered", numeric: true },
  { key: "accepted", label: "Accepted", numeric: true },
  { key: "declined", label: "Declined", numeric: true },
  { key: "ignored", label: "Ignored", numeric: true },
  { key: "preempted", label: "Preempted", numeric: true },
  { key: "accept_rate", label: "Accept %", numeric: true },
  { key: "ignore_rate", label: "Ignore %", numeric: true },
  { key: "avg_response_seconds", label: "Avg Reply", numeric: true },
];

export default function DriverOffersPage() {
  const [dateRange, setDateRange] = useState("30d");
  const [areaId, setAreaId] = useState<string>(ALL_AREAS);
  const [areas, setAreas] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<{ id: string; name: string } | null>(null);

  const [data, setData] = useState<any>(null);
  const [trend, setTrend] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const svcArea = areaId === ALL_AREAS ? undefined : areaId;

  useEffect(() => {
    getServiceAreas().then((a) => setAreas(Array.isArray(a) ? a : [])).catch(() => setAreas([]));
  }, []);

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [stats, tr] = await Promise.all([
        getDriverOfferStats(dateRange, svcArea),
        getDriverOfferTrends(dateRange, { serviceAreaId: svcArea, driverId: selected?.id }),
      ]);
      setData(stats);
      setTrend(tr);
    } catch (e: any) {
      setData(null);
      setError(e?.message || "Failed to load offer analytics. Please try again.");
    } finally {
      setLoading(false);
    }
  }, [dateRange, svcArea, selected?.id]);

  useEffect(() => { fetchData(); }, [fetchData]);
  // Reset to first page when the filters/search change.
  useEffect(() => { setPage(1); }, [dateRange, areaId, search]);

  const totals = data?.totals || {};
  const kpis = [
    { label: "Offers Sent", value: totals.offered ?? 0, Icon: Send, cls: "text-foreground" },
    { label: "Accepted", value: totals.accepted ?? 0, Icon: CheckCircle, cls: "text-emerald-600" },
    { label: "Declined", value: totals.declined ?? 0, Icon: XCircle, cls: "text-red-600" },
    { label: "Ignored", value: totals.ignored ?? 0, Icon: Clock, cls: "text-amber-600" },
  ];

  const filtered = useMemo(() => {
    const rows: any[] = Array.isArray(data?.drivers) ? [...data.drivers] : [];
    const q = search.trim().toLowerCase();
    return q ? rows.filter((d) => (d.name || "").toLowerCase().includes(q) || (d.driver_id || "").toLowerCase().includes(q)) : rows;
  }, [data, search]);

  const { sorted, sort, toggle } = useTableSort(filtered, { key: "offered", dir: "desc" });

  const pageCount = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE));
  const pageRows = sorted.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  return (
    <div className="space-y-6">
      {/* Header + filters */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold">Driver Offer Analytics</h1>
          <p className="text-sm text-muted-foreground">
            Dispatch funnel per driver — who accepts, declines, ignores, or is preempted.
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <Select value={areaId} onValueChange={setAreaId}>
            <SelectTrigger className="w-44" aria-label="Filter by service area">
              <span className="flex items-center gap-1.5 truncate"><MapPin className="h-3.5 w-3.5 shrink-0" /><SelectValue /></span>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_AREAS}>All service areas</SelectItem>
              {areas.map((a) => (
                <SelectItem key={a.id} value={a.id}>{a.name || a.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={dateRange} onValueChange={setDateRange}>
            <SelectTrigger className="w-32" aria-label="Filter by date range"><SelectValue /></SelectTrigger>
            <SelectContent>
              {DATE_RANGES.map((r) => (
                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button onClick={fetchData} className="flex items-center gap-1.5 text-sm border rounded-lg px-3 py-2 hover:bg-muted transition-colors">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      {/* KPI tiles */}
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

      {/* Trend chart */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <BarChart3 className="h-4 w-4 text-primary" />
            {selected ? `Offer Trend — ${selected.name}` : "Offer Trend (all drivers)"}
          </CardTitle>
          {selected && (
            <button onClick={() => setSelected(null)} className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              <X className="h-3.5 w-3.5" /> Clear driver
            </button>
          )}
        </CardHeader>
        <CardContent>
          {!trend?.daily_chart?.length ? (
            <div className="py-12 text-center text-sm text-muted-foreground">No offer activity in this window.</div>
          ) : (
            <ResponsiveContainer width="100%" height={240}>
              <BarChart data={trend.daily_chart}>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 10, border: "1px solid hsl(var(--border))", background: "hsl(var(--card))" }} cursor={{ fill: "hsl(var(--muted))" }} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="accepted" stackId="o" fill="#10b981" name="Accepted" radius={[0, 0, 0, 0]} />
                <Bar dataKey="declined" stackId="o" fill="#ef4444" name="Declined" />
                <Bar dataKey="ignored" stackId="o" fill="#f59e0b" name="Ignored" />
                <Bar dataKey="preempted" stackId="o" fill="#3b82f6" name="Preempted" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* Per-driver table */}
      <Card>
        <CardHeader className="flex-row items-center justify-between space-y-0 gap-3">
          <CardTitle className="text-base">Per-Driver Breakdown {data?.total_drivers ? `(${data.total_drivers})` : ""}</CardTitle>
          <div className="relative w-56">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
            <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search driver…" className="pl-8 h-9" />
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="py-16 text-center text-sm text-muted-foreground">Loading…</div>
          ) : error ? (
            <div className="py-16 text-center space-y-3">
              <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
              <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
          ) : sorted.length === 0 ? (
            <div className="py-16 text-center text-sm text-muted-foreground">{search ? "No drivers match your search." : "No offers in this window."}</div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    {COLUMNS.map((c) => (
                      <SortableHead
                        key={c.key}
                        column={c.key}
                        sort={sort}
                        onSort={toggle}
                        align={c.numeric ? "right" : "left"}
                      >
                        {c.label}
                      </SortableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageRows.map((d) => (
                    <TableRow
                      key={d.driver_id}
                      onClick={() => setSelected({ id: d.driver_id, name: d.name })}
                      className={`cursor-pointer ${selected?.id === d.driver_id ? "bg-primary/5" : ""}`}
                      title="Click to see this driver's trend"
                    >
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

              {/* Pagination */}
              <div className="flex items-center justify-between mt-4 text-sm text-muted-foreground">
                <span>
                  Showing {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, sorted.length)} of {sorted.length}
                </span>
                <div className="flex items-center gap-2">
                  <button disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}
                    className="flex items-center gap-1 border rounded-lg px-2.5 py-1.5 disabled:opacity-40 hover:bg-muted transition-colors">
                    <ChevronLeft className="h-4 w-4" /> Prev
                  </button>
                  <span className="tabular-nums">Page {page} / {pageCount}</span>
                  <button disabled={page >= pageCount} onClick={() => setPage((p) => Math.min(pageCount, p + 1))}
                    className="flex items-center gap-1 border rounded-lg px-2.5 py-1.5 disabled:opacity-40 hover:bg-muted transition-colors">
                    Next <ChevronRight className="h-4 w-4" />
                  </button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
