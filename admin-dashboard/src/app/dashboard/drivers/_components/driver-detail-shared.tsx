// Small presentational primitives + the work-authorization helper shared
// across the drivers detail slideout (page.tsx) and the detail sub-tabs.
// Pure code motion out of drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Copy } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";

// Blank-name fallback for a driver row/detail — mirrors the rider-facing
// pattern already used in dashboard/users/page.tsx (`|| email || phone`).
// Reachable today: the legacy driver importer writes "" (not skipped, not
// null) for a blank full_name, so `${first_name} ${last_name}` alone can
// render as a bare, unlabeled space with no visible content.
export function driverDisplayName(d: { first_name?: string | null; last_name?: string | null; email?: string | null; phone?: string | null } | null | undefined): string {
    if (!d) return "";
    return `${d.first_name || ""} ${d.last_name || ""}`.trim() || d.email || d.phone || "";
}
export function QuickStat({ icon: Icon, color, bg, label, value, sub, subTone }: { icon: any; color: string; bg: string; label: string; value: string; sub?: string; subTone?: "amber" | "muted" }) {
    const subClass = subTone === "amber" ? "text-warning" : "text-muted-foreground";
    return (
        <div className={`${bg} rounded-xl p-3 text-center`}>
            <Icon className={`h-4 w-4 ${color} mx-auto mb-1`} />
            <p className="text-sm font-bold">{value}</p>
            <p className="text-[10px] text-muted-foreground">{label}</p>
            {sub && <p className={`text-[10px] mt-0.5 font-medium ${subClass}`}>{sub}</p>}
        </div>
    );
}
export function DetailSection({ title, icon: Icon, children }: { title: string; icon: any; children: React.ReactNode }) {
    return (
        <div>
            <div className="flex items-center gap-2 mb-2.5">
                <div className="w-6 h-6 rounded-md bg-muted/60 flex items-center justify-center shrink-0">
                    <Icon className="h-3.5 w-3.5 text-muted-foreground" />
                </div>
                <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
            </div>
            <div className="bg-muted/10 rounded-xl p-3.5 border border-border/60">
                {children}
            </div>
        </div>
    );
}
/* ── Work authorization ───────────────────────────────────────────────
 * One canonical status. `is_permanent_resident` / `is_citizen` are derived
 * columns, not independently editable fields — they used to render as three
 * separate "Unknown" rows that could disagree with each other.
 * Mirrors routes/admin/drivers.py::work_authorization_view; the backend ships
 * the same projection as `work_authorization` on every driver row and this
 * local copy only recomputes it after an optimistic post-save merge. */
export const WORK_AUTH_LABELS: Record<string, string> = {
    citizen: "Canadian citizen",
    permanent_resident: "Permanent resident",
    indefinite: "Work permit — no expiry",
    expiring: "Work permit — expires",
    unknown: "Unknown",
};
export type WorkAuthView = { status: string; label: string; citizen: string; permanent_resident: string; expires_at: string | null };

export function workAuthLocal(driver: any): WorkAuthView {
    const raw = String(driver?.work_authorization_status || "").toLowerCase();
    let status = WORK_AUTH_LABELS[raw] ? raw : "unknown";
    // Legacy rows imported before the status column existed carry only the
    // booleans — promote them so those drivers do not read as "Unknown".
    if (status === "unknown") {
        if (driver?.is_citizen === true) status = "citizen";
        else if (driver?.is_permanent_resident === true) status = "permanent_resident";
    }
    const flag = (matches: boolean) => (status === "unknown" ? "unknown" : matches ? "yes" : "not_applicable");
    return {
        status,
        label: WORK_AUTH_LABELS[status],
        citizen: flag(status === "citizen"),
        permanent_resident: flag(status === "permanent_resident"),
        expires_at: status === "expiring" ? (driver?.work_eligibility_expiry_date ?? null) : null,
    };
}

/** Backend projection when present, local mirror otherwise. */
export const workAuth = (driver: any): WorkAuthView => (driver?.work_authorization as WorkAuthView) || workAuthLocal(driver);

export const WORK_AUTH_FLAG_LABELS: Record<string, string> = { yes: "Yes", not_applicable: "Not applicable", unknown: "Unknown" };
export function DetailField({ icon: Icon, label, value, mono }: { icon: any; label: string; value: string; mono?: boolean }) {
    return (
        <div className="bg-background border border-border/60 rounded-lg px-3 py-2.5 flex items-center gap-3 min-w-0">
            <div className="shrink-0 w-7 h-7 rounded-md bg-muted/50 flex items-center justify-center">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="min-w-0">
                <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide leading-none">{label}</p>
                <p className={`text-sm font-medium truncate leading-tight mt-1 ${mono ? "font-mono tracking-wide text-xs" : ""}`}>{value}</p>
            </div>
        </div>
    );
}
export function CopyableField({ icon: Icon, label, value }: { icon: any; label: string; value?: string }) {
    const { toast } = useToast();
    const display = value || "—";
    const canCopy = !!value;
    const copy = () => {
        if (!canCopy) return;
        navigator.clipboard.writeText(value!);
        toast({ description: `${label} copied`, duration: 1500 });
    };
    return (
        <button
            type="button"
            onClick={copy}
            disabled={!canCopy}
            className="text-left bg-background border border-border/60 rounded-lg px-3 py-2.5 flex items-center gap-3 min-w-0 w-full hover:bg-muted/30 transition-colors group disabled:cursor-default disabled:opacity-70 disabled:hover:bg-transparent"
        >
            <div className="shrink-0 w-7 h-7 rounded-md bg-muted/50 flex items-center justify-center">
                <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <div className="min-w-0 flex-1">
                <p className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide leading-none">{label}</p>
                <p className="text-sm font-medium truncate leading-tight mt-1">{display}</p>
            </div>
            {canCopy && <Copy className="h-3.5 w-3.5 text-muted-foreground/30 group-hover:text-muted-foreground shrink-0 transition-colors" />}
        </button>
    );
}
export function EditField({ label, value, onChange, type = "text", placeholder, hint }: { label: string; value: string; onChange: (v: string) => void; type?: string; placeholder?: string; hint?: string }) {
    return (
        <div>
            <label className="text-[11px] text-muted-foreground mb-1 block">{label}</label>
            <Input type={type} value={value} placeholder={placeholder} onChange={e => onChange(e.target.value)} className="h-9 text-sm" />
            {hint && <p className="text-[10px] text-muted-foreground mt-1">{hint}</p>}
        </div>
    );
}
export function EditBooleanField({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
    return (
        <div>
            <label className="text-[11px] text-muted-foreground mb-1 block">{label}</label>
            <Select value={value || "unknown"} onValueChange={v => onChange(v === "unknown" ? "" : v)}>
                <SelectTrigger className="h-9 text-sm"><SelectValue /></SelectTrigger>
                <SelectContent>
                    <SelectItem value="unknown">Unknown</SelectItem>
                    <SelectItem value="true">Yes</SelectItem>
                    <SelectItem value="false">No</SelectItem>
                </SelectContent>
            </Select>
        </div>
    );
}
