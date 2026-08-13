/**
 * A-P3-7: Smoke tests for admin dashboard pages.
 *
 * Strategy: render each page in a fully-mocked environment and assert it does
 * not throw. This exercises the module-level initialisation, hook wiring, and
 * conditional render paths without requiring a real backend or browser.
 *
 * Coverage target: 60% lines/statements on src/app/dashboard/** (vitest.config.ts).
 */

import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, act } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Common mocks applied before any page import
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), back: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/dashboard",
  redirect: vi.fn(),
  notFound: vi.fn(),
}));

vi.mock("next/link", () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock("next/image", () => ({
  default: (props: React.ImgHTMLAttributes<HTMLImageElement>) => <img {...props} />,
}));

vi.mock("next/dynamic", () => ({
  default: () => {
    const Stub = () => <div data-component="DynamicImport" />;
    return Stub;
  },
}));

vi.mock("@/store/authStore", () => {
  const state = {
    token: "fake-token",
    user: { id: "admin-001", email: "admin@spinr.ca", role: "super_admin", modules: ["dashboard"] },
    setToken: vi.fn(),
    setUser: vi.fn(),
    logout: vi.fn(),
  };
  return {
    useAuthStore: (selector?: (s: typeof state) => unknown) =>
      typeof selector === "function" ? selector(state) : state,
  };
});

// Stub every API function to avoid real network calls.
// importOriginal enumerates the real module's exports so Vitest 4 strict-ESM
// validation passes (Proxy-based mocks don't satisfy the named-export check).
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<Record<string, unknown>>();
  const noop = vi.fn().mockResolvedValue({});
  const list = vi.fn().mockResolvedValue([]);
  // Shape-specific overrides for APIs whose callers destructure the response.
  const overrides: Record<string, unknown> = {
    // Own instance (not the shared `list` fn) so a test can assert on this
    // endpoint specifically rather than on every get* call in the barrel.
    getSurgeStatus: vi.fn().mockResolvedValue([]),
    getRides: vi.fn().mockResolvedValue({ rides: [], total_count: 0 }),
    getDrivers: vi.fn().mockResolvedValue({ drivers: [], total_count: 0 }),
    getKybReverificationDue: vi.fn().mockResolvedValue({ threshold_months: 12, count: 0, companies: [] }),
    getAuditLogTopActors: vi.fn().mockResolvedValue({
      days: 7,
      window_start: "",
      rows_scanned: 0,
      rows_scanned_capped: false,
      actors: [],
    }),
  };
  return {
    ...Object.fromEntries(
      Object.entries(actual).map(([key, val]) => {
        if (typeof val !== "function") return [key, val];
        if (key.startsWith("get") || key.startsWith("list") || key.startsWith("fetch"))
          return [key, list];
        return [key, noop];
      })
    ),
    ...overrides,
  };
});

vi.mock("@/lib/utils", () => ({
  cn: (...args: unknown[]) => args.filter(Boolean).join(" "),
  formatCurrency: (n: number) => `$${Number(n).toFixed(2)}`,
  formatDate: (d: string) => d,
}));

vi.mock("@/lib/export-csv", () => ({ exportToCsv: vi.fn() }));

// Stub all Lucide icons to avoid SVG rendering issues.
// Vitest 4 strict-ESM rejects Proxy-based factories; all named exports must be
// present as own properties of the returned object.
vi.mock("lucide-react", () => {
  const Icon = ({ className }: { className?: string }) => (
    <span className={className} />
  );
  return {
    Activity: Icon, AlertCircle: Icon, AlertTriangle: Icon, ArrowDown: Icon, ArrowLeft: Icon,
    ArrowUp: Icon, ArrowUpDown: Icon, Ban: Icon, Bell: Icon, BookOpen: Icon,
    Building2: Icon, Calendar: Icon, CalendarClock: Icon, CalendarRange: Icon,
    Car: Icon, Check: Icon, CheckCircle: Icon, CheckIcon: Icon,
    ChevronDown: Icon, ChevronDownIcon: Icon, ChevronLeft: Icon,
    ChevronRight: Icon, ChevronRightIcon: Icon, ChevronUp: Icon, ChevronUpIcon: Icon,
    CircleIcon: Icon, Clock: Icon, Copy: Icon, CreditCard: Icon, DollarSign: Icon,
    Download: Icon, Edit: Icon, ExternalLink: Icon, Eye: Icon, EyeOff: Icon,
    FileCheck: Icon, FileQuestion: Icon, FileText: Icon, FileWarning: Icon,
    Flag: Icon, Gift: Icon, GitCompareArrows: Icon, Globe: Icon, HelpCircle: Icon,
    Image: Icon, ImageIcon: Icon, LifeBuoy: Icon, Loader: Icon, Loader2: Icon, Lock: Icon, Mail: Icon,
    MapPin: Icon, MessageSquare: Icon, Minus: Icon, Navigation: Icon,
    PackageSearch: Icon, Pause: Icon, PauseCircle: Icon, Pencil: Icon,
    Phone: Icon, Plane: Icon, PlayCircle: Icon, Plus: Icon, Radar: Icon,
    Radio: Icon, RefreshCw: Icon, Save: Icon, ScrollText: Icon, Search: Icon,
    Send: Icon, Settings: Icon, Shield: Icon, ShieldAlert: Icon,
    ShieldCheck: Icon, ShieldOff: Icon, SlidersHorizontal: Icon, Star: Icon,
    Tag: Icon, Target: Icon, ToggleLeft: Icon, ToggleRight: Icon, Trash2: Icon,
    TrendingDown: Icon, TrendingUp: Icon,
    User: Icon, UserCheck: Icon, UserPlus: Icon, UserX: Icon, Users: Icon, Wallet: Icon,
    Wifi: Icon, WifiOff: Icon, X: Icon, XCircle: Icon, XIcon: Icon,
    BarChart3: Icon, Ticket: Icon, Zap: Icon, ZoomIn: Icon,
    // main-side additions (rides PlusCircle, earnings Filter) + AI console icons
    Filter: Icon, PlusCircle: Icon, Bot: Icon, MessageSquarePlus: Icon, Sparkles: Icon,
    // drivers Training (LMS) tab
    GraduationCap: Icon, Award: Icon, Maximize2: Icon,
    // bulk import page + manual upload
    Upload: Icon, FileDown: Icon, CheckCircle2: Icon, Info: Icon,
    // sortable-table (used by users/earnings/analytics/corporate/disputes)
    ChevronsUpDown: Icon, Percent: Icon, Receipt: Icon, Undo2: Icon, Landmark: Icon,
    // heatmap / service-areas AD-* additions
    Flame: Icon, ArrowRightLeft: Icon,
  };
});

// Stub shadcn/ui components to lightweight HTML equivalents.
const stubComponents = (names: string[]) =>
  Object.fromEntries(
    names.map((n) => [
      n,
      ({ children, ...rest }: React.PropsWithChildren<Record<string, unknown>>) => (
        <div data-component={n} {...(rest as object)}>
          {children}
        </div>
      ),
    ])
  );

vi.mock("@/components/ui/button", () => ({
  Button: ({ children, onClick, disabled, type, variant, className }: React.PropsWithChildren<React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: string }>) => (
    <button onClick={onClick} disabled={disabled} type={type} className={className}>{children}</button>
  ),
}));

vi.mock("@/components/ui/input", () => ({
  Input: (props: React.InputHTMLAttributes<HTMLInputElement>) => <input {...props} />,
}));

vi.mock("@/components/ui/label", () => ({
  Label: ({ children, htmlFor }: React.PropsWithChildren<{ htmlFor?: string }>) => (
    <label htmlFor={htmlFor}>{children}</label>
  ),
}));

const cardStubs = stubComponents(["Card", "CardContent", "CardHeader", "CardTitle", "CardDescription", "CardFooter"]);
vi.mock("@/components/ui/card", () => cardStubs);

const tableStubs = stubComponents(["Table", "TableBody", "TableCell", "TableHead", "TableHeader", "TableRow", "TableFooter"]);
vi.mock("@/components/ui/table", () => tableStubs);

const dialogStubs = stubComponents(["Dialog", "DialogContent", "DialogHeader", "DialogTitle", "DialogDescription", "DialogFooter", "DialogTrigger"]);
vi.mock("@/components/ui/dialog", () => dialogStubs);

const selectStubs = stubComponents(["Select", "SelectContent", "SelectItem", "SelectTrigger", "SelectValue", "SelectGroup"]);
vi.mock("@/components/ui/select", () => ({
  ...selectStubs,
  Select: ({ children, onValueChange, value }: React.PropsWithChildren<{ onValueChange?: (v: string) => void; value?: string }>) => (
    <div data-component="Select" data-value={value}>{children}</div>
  ),
}));

const sheetStubs = stubComponents(["Sheet", "SheetContent", "SheetHeader", "SheetTitle", "SheetDescription", "SheetFooter", "SheetTrigger"]);
vi.mock("@/components/ui/sheet", () => sheetStubs);

const tabStubs = stubComponents(["Tabs", "TabsContent", "TabsList", "TabsTrigger"]);
vi.mock("@/components/ui/tabs", () => tabStubs);

const badgeStub = {
  Badge: ({ children, variant }: React.PropsWithChildren<{ variant?: string }>) => (
    <span data-variant={variant}>{children}</span>
  ),
};
vi.mock("@/components/ui/badge", () => badgeStub);

vi.mock("@/components/ui/pagination", () => ({
  Pagination: ({ page, totalCount, pageSize, onPageChange }: { page?: number; totalCount?: number; pageSize?: number; onPageChange?: (p: number) => void }) => (
    <nav data-page={page} data-total={totalCount} data-size={pageSize}>
      <button onClick={() => onPageChange?.(1)}>Next</button>
    </nav>
  ),
}));

vi.mock("@/components/ui/textarea", () => ({
  Textarea: (props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) => <textarea {...props} />,
}));

vi.mock("@/components/ui/switch", () => ({
  Switch: ({ onCheckedChange, checked }: { onCheckedChange?: (c: boolean) => void; checked?: boolean }) => (
    <input type="checkbox" checked={checked} onChange={(e) => onCheckedChange?.(e.target.checked)} />
  ),
}));

vi.mock("@/components/ui/checkbox", () => ({
  Checkbox: ({ onCheckedChange, checked }: { onCheckedChange?: (c: boolean) => void; checked?: boolean }) => (
    <input type="checkbox" checked={!!checked} onChange={(e) => onCheckedChange?.(e.target.checked)} />
  ),
}));

vi.mock("@/components/ui/separator", () => ({
  Separator: () => <hr />,
}));

vi.mock("@/components/ui/skeleton", () => ({
  Skeleton: ({ className }: { className?: string }) => <div className={className} aria-label="loading" />,
}));

vi.mock("@/components/ui/tooltip", () => ({
  Tooltip: ({ children }: React.PropsWithChildren) => <>{children}</>,
  TooltipContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  TooltipProvider: ({ children }: React.PropsWithChildren) => <>{children}</>,
  TooltipTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));

vi.mock("@/components/ui/popover", () => ({
  Popover: ({ children }: React.PropsWithChildren) => <>{children}</>,
  PopoverContent: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  PopoverTrigger: ({ children }: React.PropsWithChildren) => <>{children}</>,
}));

vi.mock("@/components/ui/calendar", () => ({
  Calendar: () => <div data-component="Calendar" />,
}));

vi.mock("@/components/ui/alert", () => ({
  Alert: ({ children }: React.PropsWithChildren) => <div role="alert">{children}</div>,
  AlertDescription: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
  AlertTitle: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));

vi.mock("@/components/ui/progress", () => ({
  Progress: ({ value }: { value?: number }) => <div role="progressbar" aria-valuenow={value} />,
}));

// Stub recharts (used by analytics/earnings/surge pages)
vi.mock("recharts", () => ({
  LineChart: ({ children }: React.PropsWithChildren) => <div data-chart="line">{children}</div>,
  BarChart: ({ children }: React.PropsWithChildren) => <div data-chart="bar">{children}</div>,
  AreaChart: ({ children }: React.PropsWithChildren) => <div data-chart="area">{children}</div>,
  PieChart: ({ children }: React.PropsWithChildren) => <div data-chart="pie">{children}</div>,
  Line: () => null,
  Bar: () => null,
  Area: () => null,
  Pie: () => null,
  Cell: () => null,
  XAxis: () => null,
  YAxis: () => null,
  CartesianGrid: () => null,
  Tooltip: () => null,
  Legend: () => null,
  ReferenceLine: () => null,
  ResponsiveContainer: ({ children }: React.PropsWithChildren) => <div>{children}</div>,
}));

// Stub HeatMap component (used by heatmap page, dynamically imported)
vi.mock("@/components/heat-map", () => ({
  default: () => <div data-sub="HeatMap" />,
}));

// Stub the per-domain API submodules that pages import directly (bypassing the
// "@/lib/api" barrel). These MUST be built from the real module via
// importOriginal: a hand-written literal silently drops every export it forgets,
// and because the barrel re-exports these same module ids, a dropped export
// becomes `undefined` for barrel importers too — which is how an incomplete
// settings-ai mock broke the unrelated /dashboard/settings page test.
const wrapApiModule = async (
  importOriginal: () => Promise<Record<string, unknown>>,
  overrides: Record<string, unknown> = {},
) => {
  const actual = await importOriginal();
  const noop = vi.fn().mockResolvedValue({});
  const list = vi.fn().mockResolvedValue([]);
  return {
    ...Object.fromEntries(
      Object.entries(actual).map(([key, val]) => {
        if (typeof val !== "function") return [key, val];
        return [key, /^(get|list|fetch)/.test(key) ? list : noop];
      })
    ),
    ...overrides,
  };
};

vi.mock("@/lib/api/settings-ai", (io) =>
  wrapApiModule(io as () => Promise<Record<string, unknown>>, {
    getSettings: vi.fn().mockResolvedValue({}),
    updateSettings: vi.fn().mockResolvedValue({}),
  })
);

vi.mock("@/lib/api/analytics-payouts", (io) =>
  wrapApiModule(io as () => Promise<Record<string, unknown>>, {
    getSurgeHistory: vi.fn().mockResolvedValue([]),
    getDriverOfferTrends: vi.fn().mockResolvedValue({ daily_chart: [] }),
    getDemandForecast: vi.fn().mockResolvedValue({}),
  })
);

// Stub Google Maps (used by heatmap/service-areas)
vi.mock("@react-google-maps/api", () => ({
  GoogleMap: ({ children }: React.PropsWithChildren) => <div data-component="GoogleMap">{children}</div>,
  useLoadScript: () => ({ isLoaded: false, loadError: null }),
  HeatmapLayer: () => null,
  Marker: () => null,
  InfoWindow: () => null,
  Circle: () => null,
  Polygon: () => null,
}));

// Stub date-fns and date-fns-tz
vi.mock("date-fns", () => ({
  format: (d: Date, _fmt: string) => d?.toISOString?.() ?? "",
  parseISO: (s: string) => new Date(s),
  subDays: (d: Date, n: number) => new Date(d.getTime() - n * 86400000),
  addDays: (d: Date, n: number) => new Date(d.getTime() + n * 86400000),
  startOfDay: (d: Date) => d,
  endOfDay: (d: Date) => d,
  isAfter: () => false,
  isBefore: () => false,
  differenceInDays: () => 0,
}));

// Stub sub-components that pages import (they'd pull in more deps)
const pageSubComponentStub = (name: string) => ({ default: () => <div data-sub={name} /> });

vi.mock("@/app/dashboard/drivers/_components/driver-stats-cards", () => pageSubComponentStub("DriverStatsCards"));
vi.mock("@/app/dashboard/drivers/_components/driver-charts", () => pageSubComponentStub("DriverCharts"));
vi.mock("@/app/dashboard/drivers/_components/area-stats-table", () => pageSubComponentStub("AreaStatsTable"));
vi.mock("@/app/dashboard/drivers/_components/driver-action-bar", () => pageSubComponentStub("DriverActionBar"));
vi.mock("@/app/dashboard/drivers/_components/driver-notes", () => pageSubComponentStub("DriverNotes"));
vi.mock("@/app/dashboard/drivers/_components/driver-timeline", () => pageSubComponentStub("DriverTimeline"));
vi.mock("@/app/dashboard/rides/_components/ride-stats-cards", () => ({
  default: () => <div data-sub="RideStatsCards" />,
  RidesChart: () => <div data-sub="RidesChart" />,
}));
vi.mock("@/app/dashboard/rides/_components/ride-list", () => pageSubComponentStub("RideList"));
vi.mock("@/app/dashboard/rides/_components/ride-detail-modal", () => pageSubComponentStub("RideDetailModal"));

// Monitoring page: stub WebSocket hook + heavy sub-components to prevent hangs
vi.mock("@/hooks/use-monitoring-socket", () => ({
  useMonitoringSocket: () => ({ status: "disconnected" }),
}));
vi.mock("@/app/dashboard/monitoring/monitoring-map", () => ({
  MonitoringMap: () => <div data-sub="MonitoringMap" />,
}));
vi.mock("@/app/dashboard/monitoring/toolbar", () => ({
  MonitoringToolbar: () => <div data-sub="MonitoringToolbar" />,
}));
vi.mock("@/app/dashboard/monitoring/driver-panel", () => ({
  DriverPanel: () => <div data-sub="DriverPanel" />,
}));
vi.mock("@/app/dashboard/monitoring/ride-panel", () => ({
  RidePanel: () => <div data-sub="RidePanel" />,
}));

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

async function renderPage(Component: React.ComponentType): Promise<void> {
  await act(async () => {
    render(<Component />);
  });
}

// ---------------------------------------------------------------------------
// Smoke tests — one describe per page
// ---------------------------------------------------------------------------

describe("Dashboard page — /dashboard (home)", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/rides", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/rides/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });

  it("initialises with empty rides list", async () => {
    const { default: Page } = await import("@/app/dashboard/rides/page");
    await act(async () => { render(<Page />); });
    // Page should render something (not just blank)
    expect(document.body.innerHTML.length).toBeGreaterThan(0);
  });
});

describe("Dashboard page — /dashboard/drivers", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/drivers/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/users", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/users/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/earnings", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/earnings/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/analytics", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/analytics/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/settings", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/settings/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/staff", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/staff/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/promotions", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/promotions/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/surge", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/surge/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/corporate-accounts", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/corporate-accounts/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/disputes", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/disputes/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/support", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/support/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/audit-logs", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/audit-logs/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/notifications", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/notifications/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/monitoring", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/monitoring/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/documents", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/documents/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/drivers/import", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/drivers/import/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/bulk-operations", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/bulk-operations/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});

describe("Dashboard page — /dashboard/heatmap", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/heatmap/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });

  it("shows the live demand section heading", async () => {
    // Renamed from "Unmet Demand": demand_count includes rides that already
    // have a driver, so the old label overstated stranded riders.
    const { default: Page } = await import("@/app/dashboard/heatmap/page");
    await act(async () => { render(<Page />); });
    expect(screen.getByText("Live Demand Pressure")).toBeTruthy();
  });

  it("does not poll for demand until live updates are switched on", async () => {
    // The poll previously ran from mount for every open tab, forever, hitting
    // two of the most expensive admin endpoints whether or not anyone looked.
    const api = await import("@/lib/api");
    const spy = vi.mocked(api.getSurgeStatus);
    spy.mockClear();
    const { default: Page } = await import("@/app/dashboard/heatmap/page");
    await act(async () => { render(<Page />); });
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("Dashboard page — /dashboard/service-areas", () => {
  it("renders without crashing", async () => {
    const { default: Page } = await import("@/app/dashboard/service-areas/page");
    await expect(renderPage(Page)).resolves.not.toThrow();
  });
});
