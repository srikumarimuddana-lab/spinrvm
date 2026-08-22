"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
    X,
    Check,
    XCircle,
    ChevronLeft,
    ChevronRight,
    FileText,
    Calendar,
    ExternalLink,
    Download,
    Bell,
    BellOff,
    AlertCircle,
    Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/components/ui/use-toast";
import {
    downloadDriverDocument,
    getDriverDocuments,
    reviewDocument,
    type RejectTemplate,
} from "@/lib/api";

interface DocumentReviewerProps {
    open: boolean;
    driverId: string | null;
    driverName?: string | null;
    onClose: () => void;
    onAfterAction?: () => void;
}

type DocRow = {
    id: string;
    driver_id: string;
    document_type?: string;
    document_url?: string;
    status?: string;
    rejection_reason?: string | null;
    uploaded_at?: string;
    requirement_key?: string | null;
    requirement_id?: string | null;
    side?: string | null;
};

const TEMPLATES: { value: RejectTemplate; label: string; body: string }[] = [
    { value: "blurry_image", label: "Blurry image", body: "We couldn't read your document clearly. Please re-upload a sharper photo." },
    { value: "wrong_document_type", label: "Wrong document type", body: "The file you uploaded doesn't match what's required. Please upload the correct document." },
    { value: "expired", label: "Expired document", body: "Your document appears expired. Please upload a current copy to continue driving." },
    { value: "information_unclear", label: "Information unclear", body: "Some information on your document is unclear or unreadable. Please re-upload." },
    { value: "other", label: "Other (write your own)", body: "" },
];

// Mirrors the backend `_REQUIREMENT_EXPIRY_FIELD_KEYWORDS` mapping. We use
// this client-side heuristic so the reviewer can show "expiry required"
// without a separate service-area fetch — the backend remains the source
// of truth on whether the field actually propagates.
const REQUIRES_EXPIRY_KEYWORDS = ["license", "driving", "permit", "insurance", "inspection", "background", "work", "eligibility"];
const docRequiresExpiry = (doc: DocRow): boolean => {
    const hay = `${doc.document_type || ""} ${doc.requirement_key || ""}`.toLowerCase();
    return REQUIRES_EXPIRY_KEYWORDS.some((k) => hay.includes(k));
};

const isPdf = (url?: string) => !!url && /\.pdf(\?|#|$)/i.test(url);

const statusTone = (status?: string) => {
    switch (status) {
        case "approved": return "bg-success/15 text-success";
        case "rejected": return "bg-destructive/15 text-destructive";
        case "pending": return "bg-warning/15 text-warning";
        default: return "bg-muted text-muted-foreground";
    }
};

export function DocumentReviewer({ open, driverId, driverName, onClose, onAfterAction }: DocumentReviewerProps) {
    const { toast } = useToast();
    const [docs, setDocs] = useState<DocRow[]>([]);
    const [index, setIndex] = useState(0);
    const [loading, setLoading] = useState(false);
    const [busy, setBusy] = useState(false);
    const [mode, setMode] = useState<"idle" | "approve" | "reject">("idle");
    const [expiry, setExpiry] = useState("");
    const [reason, setReason] = useState("");
    const [template, setTemplate] = useState<RejectTemplate>("blurry_image");
    const [notify, setNotify] = useState(true);
    const reasonRef = useRef<HTMLTextAreaElement>(null);
    const [downloading, setDownloading] = useState(false);
    const dialogRef = useRef<HTMLDivElement>(null);
    // The element focused just before the modal opened, so we can hand
    // focus back to it on close (e.g. the driver row's "Review" button).
    const previouslyFocusedRef = useRef<Element | null>(null);

    const current = docs[index];

    const handleDownload = async () => {
        if (!current) return;
        setDownloading(true);
        try {
            await downloadDriverDocument(current.id, driverName || "", current.document_type || "document");
        } catch (e: any) {
            toast({ title: "Download failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setDownloading(false);
        }
    };
    const needsExpiry = current ? docRequiresExpiry(current) : false;

    const resetDocState = useCallback(() => {
        setMode("idle");
        setExpiry("");
        setReason("");
        setTemplate("blurry_image");
        setNotify(true);
    }, []);

    useEffect(() => {
        if (!open || !driverId) return;
        setLoading(true);
        setDocs([]);
        setIndex(0);
        resetDocState();
        getDriverDocuments(driverId)
            .then((arr) => {
                const list: DocRow[] = Array.isArray(arr) ? arr : [];
                setDocs(list);
                const firstPending = list.findIndex((d) => d.status === "pending");
                setIndex(firstPending >= 0 ? firstPending : 0);
            })
            .catch((e) => toast({ title: "Could not load documents", description: String(e?.message || e), variant: "destructive" }))
            .finally(() => setLoading(false));
    }, [open, driverId, toast, resetDocState]);

    const goPrev = useCallback(() => { setIndex((i) => Math.max(0, i - 1)); resetDocState(); }, [resetDocState]);
    const goNext = useCallback(() => { setIndex((i) => Math.min(docs.length - 1, i + 1)); resetDocState(); }, [docs.length, resetDocState]);

    const submit = useCallback(async (status: "approved" | "rejected") => {
        if (!current) return;
        if (status === "approved" && needsExpiry && !expiry) {
            toast({ title: "Expiry date required", description: "This document needs an expiry date before approval.", variant: "destructive" });
            return;
        }
        if (status === "rejected") {
            const templateMeta = TEMPLATES.find((t) => t.value === template);
            const finalReason = template === "other" ? reason.trim() : (reason.trim() || templateMeta?.body || "");
            if (!finalReason) {
                toast({ title: "Reason required", description: "Pick a template or write a reason.", variant: "destructive" });
                return;
            }
            setBusy(true);
            try {
                await reviewDocument(current.id, "rejected", finalReason, undefined, {
                    notify,
                    notifyTemplate: template,
                });
                toast({ title: "Document rejected", description: notify ? "Driver was notified." : "Rejected without notification." });
            } catch (e) {
                toast({ title: "Rejection failed", description: String((e as Error).message || e), variant: "destructive" });
                setBusy(false);
                return;
            }
        } else {
            setBusy(true);
            try {
                await reviewDocument(current.id, "approved", undefined, expiry ? new Date(expiry).toISOString() : undefined);
                toast({ title: "Document approved" });
            } catch (e) {
                toast({ title: "Approval failed", description: String((e as Error).message || e), variant: "destructive" });
                setBusy(false);
                return;
            }
        }

        const newStatus = status;
        setDocs((prev) => prev.map((d, i) => (i === index ? { ...d, status: newStatus } : d)));
        onAfterAction?.();
        setBusy(false);

        const nextPending = docs.findIndex((d, i) => i > index && d.status === "pending");
        if (nextPending >= 0) { setIndex(nextPending); resetDocState(); }
        else resetDocState();
    }, [current, needsExpiry, expiry, template, reason, notify, index, docs, onAfterAction, toast, resetDocState]);

    useEffect(() => {
        if (!open) return;
        const handler = (e: KeyboardEvent) => {
            if (e.key === "Escape") { onClose(); return; }
            if (e.key === "Tab") {
                // Focus-trap: keep Tab/Shift+Tab cycling within the modal
                // instead of leaking focus out to the page behind it.
                const root = dialogRef.current;
                if (!root) return;
                const focusable = Array.from(
                    root.querySelectorAll<HTMLElement>(
                        'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
                    ),
                ).filter((el) => el.offsetParent !== null);
                if (focusable.length === 0) return;
                const first = focusable[0];
                const last = focusable[focusable.length - 1];
                if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                }
                return;
            }
            const tag = (document.activeElement?.tagName || "").toLowerCase();
            if (tag === "input" || tag === "textarea" || tag === "select") return;
            const k = e.key.toLowerCase();
            if (k === "j") { e.preventDefault(); goNext(); }
            else if (k === "k") { e.preventDefault(); goPrev(); }
            else if (k === "a" && current?.status === "pending") {
                e.preventDefault();
                if (mode !== "approve") setMode("approve");
                else submit("approved");
            }
            else if (k === "r" && current?.status === "pending") {
                e.preventDefault();
                if (mode !== "reject") { setMode("reject"); setTimeout(() => reasonRef.current?.focus(), 0); }
                else submit("rejected");
            }
        };
        document.addEventListener("keydown", handler);
        return () => document.removeEventListener("keydown", handler);
    }, [open, onClose, goNext, goPrev, current?.status, mode, submit]);

    // Focus-trap-in / focus-restore-out: move focus into the modal when it
    // opens (screen-reader users otherwise stay anchored on the trigger
    // behind an aria-modal dialog) and hand it back to whatever triggered
    // the modal once it closes, so keyboard/AT users aren't dropped back at
    // the top of the page.
    useEffect(() => {
        if (!open) return;
        previouslyFocusedRef.current = document.activeElement;
        const id = window.setTimeout(() => {
            const root = dialogRef.current;
            if (!root) return;
            const firstFocusable = root.querySelector<HTMLElement>(
                'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
            );
            (firstFocusable ?? root).focus();
        }, 0);
        return () => {
            window.clearTimeout(id);
            const toRestore = previouslyFocusedRef.current;
            if (toRestore instanceof HTMLElement) toRestore.focus();
        };
    }, [open]);

    const counter = useMemo(() => (docs.length ? `${index + 1} of ${docs.length}` : "0 of 0"), [docs.length, index]);

    if (!open) return null;

    return (
        <div
            ref={dialogRef}
            tabIndex={-1}
            className="fixed inset-0 z-[100] bg-background/95 backdrop-blur-sm flex flex-col outline-none"
            role="dialog"
            aria-modal="true"
            aria-label="Document reviewer"
        >
            <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-card">
                <div className="flex items-center gap-3 min-w-0">
                    <FileText className="h-5 w-5 text-muted-foreground shrink-0" />
                    <div className="min-w-0">
                        <p className="text-sm font-semibold truncate">{driverName || "Document Reviewer"}</p>
                        <p className="text-xs text-muted-foreground">{counter} • {current?.document_type || "—"}</p>
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    <span className="hidden md:inline text-[11px] text-muted-foreground">A approve · R reject · J/K next/prev · Esc close</span>
                    <Button variant="ghost" size="sm" onClick={onClose}>
                        <X className="h-4 w-4" />
                    </Button>
                </div>
            </div>

            {loading ? (
                <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    <Loader2 className="h-5 w-5 animate-spin mr-2" /> Loading documents…
                </div>
            ) : !current ? (
                <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    <div className="text-center">
                        <FileText className="h-10 w-10 mx-auto opacity-30 mb-3" />
                        <p className="text-sm font-medium">No documents to review</p>
                        <p className="text-xs mt-1">This driver hasn&apos;t uploaded any documents yet.</p>
                    </div>
                </div>
            ) : (
                <div className="flex-1 grid grid-cols-1 lg:grid-cols-[1.6fr_1fr] overflow-hidden">
                    <div className="bg-muted/30 flex items-center justify-center p-6 overflow-auto relative">
                        {current.document_url ? (
                            isPdf(current.document_url) ? (
                                <embed src={current.document_url} type="application/pdf" className="w-full h-full rounded-lg shadow-md bg-white" />
                            ) : (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img src={current.document_url} alt={current.document_type || "Document"} className="max-w-full max-h-full object-contain rounded-lg shadow-md" />
                            )
                        ) : (
                            <div className="text-center text-muted-foreground">
                                <AlertCircle className="h-10 w-10 mx-auto opacity-30 mb-3" />
                                <p className="text-sm">No file URL on this document.</p>
                            </div>
                        )}
                        {current.document_url && (
                            <div className="absolute top-3 right-3 flex items-center gap-1.5">
                                {/* "Open original" only ever displayed the file.
                                    Saving it to disk — to attach to a regulator
                                    email — had no affordance here either. */}
                                <button
                                    type="button"
                                    onClick={handleDownload}
                                    disabled={downloading}
                                    className="bg-card/90 hover:bg-card border border-border rounded-lg px-2 py-1 text-xs font-medium flex items-center gap-1.5 disabled:opacity-60"
                                >
                                    {downloading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />} Download
                                </button>
                                <a href={current.document_url} target="_blank" rel="noreferrer" className="bg-card/90 hover:bg-card border border-border rounded-lg px-2 py-1 text-xs font-medium flex items-center gap-1.5">
                                    <ExternalLink className="h-3.5 w-3.5" /> Open original
                                </a>
                            </div>
                        )}
                    </div>

                    <div className="border-l border-border bg-card flex flex-col overflow-hidden">
                        <div className="px-5 py-4 border-b border-border space-y-3">
                            <div className="flex items-center justify-between gap-2">
                                <div className="min-w-0">
                                    <h3 className="text-sm font-semibold truncate">{current.document_type || "Document"}</h3>
                                    {current.side && <p className="text-xs text-muted-foreground">Side: {current.side}</p>}
                                </div>
                                <Badge className={statusTone(current.status)}>{current.status || "unknown"}</Badge>
                            </div>
                            <div className="grid grid-cols-2 gap-3 text-xs">
                                <div>
                                    <p className="text-muted-foreground">Uploaded</p>
                                    <p className="font-medium">{current.uploaded_at ? new Date(current.uploaded_at).toLocaleDateString() : "—"}</p>
                                </div>
                                <div>
                                    <p className="text-muted-foreground">Requirement</p>
                                    <p className="font-medium truncate">{current.requirement_key || current.requirement_id || "—"}</p>
                                </div>
                            </div>
                            {current.rejection_reason && current.status === "rejected" && (
                                <div className="rounded-md bg-destructive/10 border border-destructive/30 px-3 py-2 text-xs text-destructive">
                                    <span className="font-semibold">Prior reason:</span> {current.rejection_reason}
                                </div>
                            )}
                        </div>

                        <div className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
                            {current.status === "pending" ? (
                                <>
                                    <div className="flex gap-2">
                                        <Button
                                            onClick={() => setMode("approve")}
                                            variant={mode === "approve" ? "default" : "outline"}
                                            className={mode === "approve" ? "flex-1 bg-emerald-600 hover:bg-emerald-700 text-white" : "flex-1"}
                                            disabled={busy}
                                        >
                                            <Check className="h-4 w-4 mr-1.5" /> Approve
                                        </Button>
                                        <Button
                                            onClick={() => { setMode("reject"); setTimeout(() => reasonRef.current?.focus(), 0); }}
                                            variant={mode === "reject" ? "default" : "outline"}
                                            className={mode === "reject" ? "flex-1 bg-red-600 hover:bg-red-700 text-white" : "flex-1"}
                                            disabled={busy}
                                        >
                                            <XCircle className="h-4 w-4 mr-1.5" /> Reject
                                        </Button>
                                    </div>

                                    {mode === "approve" && (
                                        <div className="space-y-3 rounded-lg border border-success/30 bg-success/10 p-3">
                                            <label htmlFor="reviewer-expiry" className="text-xs font-medium flex items-center gap-1.5">
                                                <Calendar className="h-3.5 w-3.5" />
                                                Expiry date {needsExpiry && <span className="text-destructive">*</span>}
                                                {!needsExpiry && <span className="text-muted-foreground font-normal">(optional)</span>}
                                            </label>
                                            <Input id="reviewer-expiry" type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} className="h-9" />
                                            <Button onClick={() => submit("approved")} disabled={busy || (needsExpiry && !expiry)} className="w-full bg-emerald-600 hover:bg-emerald-700 text-white">
                                                {busy ? <Loader2 className="h-4 w-4 animate-spin mr-1.5" /> : <Check className="h-4 w-4 mr-1.5" />}
                                                Confirm approval
                                            </Button>
                                        </div>
                                    )}

                                    {mode === "reject" && (
                                        <div className="space-y-3 rounded-lg border border-destructive/30 bg-destructive/10 p-3">
                                            <p className="text-xs font-medium">Reason template</p>
                                            <Select value={template} onValueChange={(v) => {
                                                const tmpl = v as RejectTemplate;
                                                setTemplate(tmpl);
                                                const m = TEMPLATES.find((t) => t.value === tmpl);
                                                if (m && tmpl !== "other") setReason(m.body);
                                                else if (tmpl === "other") setReason("");
                                            }}>
                                                <SelectTrigger className="h-9 text-xs"><SelectValue /></SelectTrigger>
                                                <SelectContent>
                                                    {TEMPLATES.map((t) => <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>)}
                                                </SelectContent>
                                            </Select>

                                            <label htmlFor="reviewer-reason" className="text-xs font-medium">Reason text {template === "other" && <span className="text-destructive">*</span>}</label>
                                            <Textarea id="reviewer-reason" ref={reasonRef} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Explain why this is rejected…" className="text-sm min-h-[80px]" />

                                            <label className="flex items-start gap-2 cursor-pointer text-xs">
                                                <input type="checkbox" checked={notify} onChange={(e) => setNotify(e.target.checked)} className="mt-0.5" />
                                                <span className="flex items-center gap-1.5">
                                                    {notify ? <Bell className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400" /> : <BellOff className="h-3.5 w-3.5 text-muted-foreground" />}
                                                    Notify driver via push so they can re-upload
                                                </span>
                                            </label>

                                            <Button onClick={() => submit("rejected")} disabled={busy} className="w-full bg-red-600 hover:bg-red-700 text-white">
                                                {busy ? <Loader2 className="h-4 w-4 animate-spin mr-1.5" /> : <XCircle className="h-4 w-4 mr-1.5" />}
                                                Confirm rejection
                                            </Button>
                                        </div>
                                    )}
                                </>
                            ) : (
                                <div className="rounded-lg bg-muted/30 border border-border p-3 text-xs text-muted-foreground">
                                    This document is already <span className="font-semibold text-foreground">{current.status}</span>. Use the driver detail sheet to override.
                                </div>
                            )}
                        </div>

                        <div className="border-t border-border px-5 py-3 flex items-center justify-between bg-muted/20">
                            <Button variant="ghost" size="sm" onClick={goPrev} disabled={index === 0}>
                                <ChevronLeft className="h-4 w-4 mr-1" /> Prev
                            </Button>
                            <span className="text-xs text-muted-foreground tabular-nums">{counter}</span>
                            <Button variant="ghost" size="sm" onClick={goNext} disabled={index >= docs.length - 1}>
                                Next <ChevronRight className="h-4 w-4 ml-1" />
                            </Button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}
