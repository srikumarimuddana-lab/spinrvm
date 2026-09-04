// Document/verification helpers shared by the drivers detail slideout
// (page.tsx) and its Documents/Actions tabs. Pure code motion out of
// drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import { useState } from "react";
import { downloadDriverDocument } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { CheckCircle, ExternalLink, Clock, AlertTriangle, ZoomIn, FileText, Image, CalendarRange, XCircle, Download, Loader2 } from "lucide-react";

// Match a driver_documents row to a service-area requirement using
// one consistent priority everywhere:
//   1. requirement_key — canonical slug stored since migration 28
//   2. requirement_id — UUID, or the slug treated as a legacy id
//   3. document_type exact match (label or de-snaked key)
//   4. fuzzy: slugified document_type contains slugified key
// Previously this logic lived in 4 different places with slight drift,
// which caused expiry summaries and "requires-expiry" detection to
// silently miss docs that only carried a requirement_key.
export function matchesRequirement(
    d: any,
    req: { id?: string; key: string; label?: string },
): boolean {
    if (d.requirement_key) return d.requirement_key === req.key;
    if (d.requirement_id) return d.requirement_id === req.id || d.requirement_id === req.key;
    const dt = (d.document_type || "").toLowerCase();
    const label = (req.label || "").toLowerCase();
    const keySpaced = req.key.toLowerCase().replace(/_/g, " ");
    if (dt === label || dt === keySpaced) return true;
    const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]/g, "_");
    return slug(dt).includes(slug(req.key));
}
export function VerificationSummaryCard({
    requiredDocs,
    activeDocs,
    driver,
    docKeyToExpiryField,
    onOpenDocumentsTab,
}: {
    requiredDocs: { id?: string; key: string; label: string; has_expiry: boolean }[];
    activeDocs: any[];
    driver: any;
    docKeyToExpiryField: (key: string) => string | null;
    onOpenDocumentsTab: () => void;
}) {
    const rows = requiredDocs.map(rd => {
        const matchingDocs = activeDocs.filter(d => matchesRequirement(d, rd));
        const hasApproved = matchingDocs.some(d => d.status === "approved");
        const hasPending = matchingDocs.some(d => d.status === "pending");
        const expiryField = docKeyToExpiryField(rd.key);
        const expiryVal = expiryField ? driver[expiryField] : undefined;
        const isExpired = expiryVal && new Date(expiryVal) < new Date();
        let s: "approved" | "pending" | "missing" | "expired" = "missing";
        if (isExpired) s = "expired";
        else if (hasApproved) s = "approved";
        else if (hasPending || matchingDocs.length > 0) s = "pending";
        return { rd, status: s };
    });

    const approved = rows.filter(r => r.status === "approved").length;
    const total = rows.length;
    const pending = rows.filter(r => r.status === "pending").length;
    const missing = rows.filter(r => r.status === "missing").length;
    const expired = rows.filter(r => r.status === "expired").length;
    const pct = total > 0 ? Math.round((approved / total) * 100) : 0;
    const allClear = pending === 0 && missing === 0 && expired === 0 && total > 0;

    return (
        <div className="rounded-xl border border-border bg-card overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                    <CheckCircle className={`h-4 w-4 ${allClear ? "text-success" : "text-muted-foreground"}`} />
                    <h3 className="text-sm font-semibold tracking-tight">Verification</h3>
                    <span className="text-xs text-muted-foreground">{approved} / {total} approved</span>
                </div>
                <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onOpenDocumentsTab}>
                    Open Documents
                    <ExternalLink className="h-3 w-3 ml-1" />
                </Button>
            </div>
            <div className="px-4 py-3 space-y-3">
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                        className={`h-full transition-all ${allClear ? "bg-success" : pct >= 75 ? "bg-warning" : "bg-muted-foreground/40"}`}
                        style={{ width: `${pct}%` }}
                    />
                </div>
                <div className="flex items-center gap-3 flex-wrap text-xs">
                    {pending > 0 && <span className="inline-flex items-center gap-1 text-warning"><Clock className="h-3 w-3" />{pending} pending</span>}
                    {missing > 0 && <span className="inline-flex items-center gap-1 text-destructive"><AlertTriangle className="h-3 w-3" />{missing} missing</span>}
                    {expired > 0 && <span className="inline-flex items-center gap-1 text-destructive"><AlertTriangle className="h-3 w-3" />{expired} expired</span>}
                    {allClear && <span className="inline-flex items-center gap-1 text-success"><CheckCircle className="h-3 w-3" />All required documents are approved.</span>}
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 pt-1">
                    {rows.map(({ rd, status }) => {
                        const cfg = status === "approved" ? { icon: <CheckCircle className="h-3.5 w-3.5 text-success" />, text: "text-success" }
                            : status === "pending" ? { icon: <Clock className="h-3.5 w-3.5 text-warning" />, text: "text-warning" }
                            : status === "expired" ? { icon: <AlertTriangle className="h-3.5 w-3.5 text-destructive" />, text: "text-destructive" }
                            : { icon: <div className="w-3.5 h-3.5 rounded-full border-2 border-muted-foreground/30" />, text: "text-muted-foreground" };
                        return (
                            <div key={rd.key} className="flex items-center gap-2 text-xs">
                                {cfg.icon}
                                <span className="truncate flex-1">{rd.label}</span>
                                <span className={`text-[10px] uppercase tracking-wide font-semibold ${cfg.text}`}>{status}</span>
                            </div>
                        );
                    })}
                </div>
                <div className="flex items-center justify-between pt-1 text-xs border-t border-border">
                    <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${driver.profile_image_status && driver.profile_image_status !== "rejected" ? "bg-success" : "bg-muted-foreground/30"}`} />
                        <span className="text-muted-foreground">Profile photo</span>
                    </div>
                    <div className="flex items-center gap-2">
                        <div className={`w-2 h-2 rounded-full ${driver.vehicle_photo_url ? "bg-success" : "bg-muted-foreground/30"}`} />
                        <span className="text-muted-foreground">Vehicle photo</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
export type DocSummary = {
    expiry?: string;
    docStatus: "approved" | "pending" | "rejected" | "missing";
    expiryIsLegacy: boolean;
};

export function DocExpirySummaryCard({ label, summary }: { label: string; summary: DocSummary }) {
    const { expiry, docStatus } = summary;
    const fmt = (d: string) => { try { return new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }); } catch { return d; } };
    const isExpired = expiry ? new Date(expiry) < new Date() : false;
    const daysUntil = expiry ? Math.ceil((new Date(expiry).getTime() - Date.now()) / 86400000) : null;
    const isExpiringSoon = daysUntil !== null && daysUntil > 0 && daysUntil <= 30;

    // Pick palette + copy from the highest-priority signal: status first,
    // then expiry health. Crucially, an approved doc that's missing an
    // expiry is treated as amber ("Expiry not recorded") instead of being
    // greyed-out like an upload that was never made — those are very
    // different operational states.
    let palette: "neutral" | "emerald" | "amber" | "red";
    let primary: string;
    let secondary: string | null = null;

    if (docStatus === "missing") {
        palette = "neutral";
        primary = "Not uploaded";
        secondary = "Driver has not provided this document yet";
    } else if (docStatus === "rejected") {
        palette = "red";
        primary = "Re-upload needed";
        secondary = "Previous upload was rejected";
    } else if (docStatus === "pending") {
        palette = "amber";
        primary = "Pending review";
        secondary = "Waiting for admin approval";
    } else if (docStatus === "approved" && !expiry) {
        // Approved but no expiry on file. Often a legacy approval predating
        // the per-doc expiry column; re-approve to record one.
        palette = "amber";
        primary = "Approved";
        secondary = "Expiry not recorded — re-approve to set";
    } else if (isExpired) {
        palette = "red";
        primary = "Expired";
        secondary = `Expired ${fmt(expiry!)}`;
    } else if (isExpiringSoon) {
        palette = "amber";
        primary = fmt(expiry!);
        secondary = `${daysUntil} day${daysUntil !== 1 ? "s" : ""} remaining`;
    } else {
        palette = "emerald";
        primary = fmt(expiry!);
        secondary = daysUntil !== null ? `${daysUntil} days remaining` : null;
    }

    const styles = {
        neutral: { bg: "bg-muted/30 border-border", dot: "bg-muted-foreground/40", primary: "text-muted-foreground", secondary: "text-muted-foreground" },
        emerald: { bg: "bg-success/10 border-success/30", dot: "bg-success", primary: "text-success", secondary: "text-success/70" },
        amber:   { bg: "bg-warning/10 border-warning/30", dot: "bg-warning", primary: "text-warning", secondary: "text-warning/80" },
        red:     { bg: "bg-destructive/10 border-destructive/30", dot: "bg-destructive", primary: "text-destructive", secondary: "text-destructive/80" },
    }[palette];

    return (
        <div className={`rounded-xl p-3 border ${styles.bg}`}>
            <div className="flex items-center gap-2 mb-1">
                <div className={`w-2 h-2 rounded-full ${styles.dot}`} />
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
            </div>
            <p className={`text-sm font-bold ${styles.primary}`}>{primary}</p>
            {secondary && <p className={`text-[10px] mt-0.5 ${styles.secondary}`}>{secondary}</p>}
        </div>
    );
}

export function DocCard({ d, docBusy, driverName, onPreview, onReview }: { d: any; docBusy: string | null; driverName: string; onPreview: (url: string) => void; onReview: (id: string, action: "approved" | "rejected") => void }) {
    const { toast } = useToast();
    const [downloading, setDownloading] = useState(false);

    const onDownload = async () => {
        setDownloading(true);
        try {
            await downloadDriverDocument(d.id, driverName, d.document_type || "document");
        } catch (e: any) {
            // Surfaced, not swallowed: a document that won't download is the
            // difference between filing a regulator submission and not.
            toast({ title: "Download failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setDownloading(false);
        }
    };

    const exp = d.expiry_date || d.expires_at;
    const expired = exp && new Date(exp) < new Date();
    const isImage = d.document_url && /\.(jpg|jpeg|png|gif|webp|bmp|svg)(\?|$)/i.test(d.document_url);
    const sc = d.status === "approved" && !expired ? "emerald" : d.status === "rejected" ? "red" : expired ? "red" : "amber";
    return (
        <div className="bg-card rounded-xl border overflow-hidden transition hover:shadow-md group">
            <div className="relative h-44 bg-muted/50 flex items-center justify-center overflow-hidden">
                {/* eslint-disable-next-line no-restricted-syntax -- decorative light zoom-icon chip on a dark hover overlay, not a status signal (#2816) */}
                {isImage ? (<><img src={d.document_url} alt={d.document_type||"Document"} loading="lazy" decoding="async" className="w-full h-full object-cover" onError={e=>{(e.target as HTMLImageElement).style.display='none';}} /><button onClick={()=>onPreview(d.document_url)} className="absolute inset-0 bg-black/0 group-hover:bg-black/30 transition flex items-center justify-center opacity-0 group-hover:opacity-100"><div className="bg-white/90 rounded-full p-2"><ZoomIn className="h-5 w-5 text-gray-800" /></div></button></>)
                : d.document_url ? <a href={d.document_url} target="_blank" rel="noreferrer" className="flex flex-col items-center gap-2 text-muted-foreground hover:text-foreground transition"><FileText className="h-12 w-12 opacity-40" /><span className="text-xs font-medium">Click to view</span></a>
                : <div className="flex flex-col items-center gap-2 text-muted-foreground"><Image className="h-12 w-12 opacity-20" /><span className="text-xs">No file</span></div>}
                {/* eslint-disable-next-line no-restricted-syntax -- solid-fill white-text document-status badge; token substitution risks a dark-mode contrast regression (#2816 Batch 1 finding) */}
                <div className="absolute top-2 right-2"><Badge className={`text-[10px] shadow-sm ${sc==="emerald"?"bg-emerald-500 text-white":sc==="red"?"bg-red-500 text-white":"bg-amber-500 text-white"}`}>{expired&&d.status==="approved"?"EXPIRED":d.status?.toUpperCase()}</Badge></div>
                {d.side && <div className="absolute top-2 left-2"><Badge variant="secondary" className="text-[10px] shadow-sm bg-black/60 text-white border-none">{d.side}</Badge></div>}
            </div>
            <div className="p-3 space-y-2">
                <p className="text-sm font-semibold truncate">{d.document_type||"Document"}{d.side?` (${d.side})`:""}</p>
                <div className="space-y-1">
                    {d.created_at && <p className="text-[11px] text-muted-foreground flex items-center gap-1"><CalendarRange className="h-3 w-3" />Uploaded: {new Date(d.created_at).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}</p>}
                    {exp && <p className={`text-[11px] flex items-center gap-1 ${expired?"text-destructive font-medium":"text-muted-foreground"}`}><Clock className="h-3 w-3" />Expires: {new Date(exp).toLocaleDateString("en-CA",{month:"short",day:"numeric",year:"numeric"})}{expired&&" (EXPIRED)"}</p>}
                </div>
                {d.rejection_reason && <p className="text-[11px] text-destructive bg-destructive/10 rounded-lg px-2 py-1"><AlertTriangle className="h-3 w-3 inline mr-1" />{d.rejection_reason}</p>}
                <div className="flex items-center gap-1.5 pt-1">
                    <Button variant="outline" size="xs" className="flex-1 text-success border-success/30 hover:bg-success/10" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"approved")}><CheckCircle className="h-3 w-3" /> Approve</Button>
                    <Button variant="outline" size="xs" className="flex-1 text-destructive border-destructive/30 hover:bg-destructive/10" disabled={docBusy===d.id} onClick={()=>onReview(d.id,"rejected")}><XCircle className="h-3 w-3" /> Reject</Button>
                </div>
                {/* Saving the file to disk had no affordance at all — the card
                    could only preview, approve, or reject. Admins need the
                    actual file to attach to a regulator email (e.g. sending a
                    criminal record check to SGI). */}
                <Button variant="outline" size="xs" className="w-full" disabled={!d.document_url || downloading} onClick={onDownload}>
                    {downloading ? <Loader2 className="h-3 w-3 animate-spin" /> : <Download className="h-3 w-3" />} Download file
                </Button>
            </div>
        </div>
    );
}
