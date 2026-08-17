"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getLegalDocuments, upsertLegalDocument, type LegalDocType } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { RefreshCw, Save, Check } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { useToast } from "@/components/ui/use-toast";

type Audience = "rider" | "driver";
type DocType = LegalDocType;

type Row = {
    id?: string;
    audience: Audience;
    doc_type: DocType;
    content: string;
    version?: number;
    updated_at?: string;
};

// Shared pages apply to both audiences; deactivation-appeals is driver-only
// (drivers are the ones who can be deactivated — see
// docs/legal/driver-deactivation-appeals-policy.md). Content for every row
// below is still a draft pending counsel review — see
// docs/legal/legal-text-publication-checklist.md before publishing any of
// these to production.
const SHARED_TYPES: { doc_type: DocType; label: string }[] = [
    { doc_type: "tos", label: "Terms of Service" },
    { doc_type: "privacy", label: "Privacy Policy" },
    { doc_type: "community-guidelines", label: "Community Guidelines" },
    { doc_type: "non-discrimination", label: "Non-Discrimination Policy" },
    { doc_type: "accessibility", label: "Accessibility Statement" },
    { doc_type: "cancellation-fees", label: "Cancellation & No-Show Fees" },
    { doc_type: "promotions-referral", label: "Promotions & Referral Terms" },
    { doc_type: "insurance-periods", label: "Insurance Coverage Periods" },
];

// Driver-only pages: deactivation-appeals (drivers are the ones who can be
// deactivated) and background-check-consent (drivers are the ones whose
// CRC/VSC is collected — see docs/legal/background-check-consent.md).
const DRIVER_ONLY_TYPES: { doc_type: DocType; label: string }[] = [
    { doc_type: "deactivation-appeals", label: "Driver Deactivation & Appeals Policy" },
    { doc_type: "background-check-consent", label: "Background-Check (CRC/VSC) Consent" },
];

const DOCS: { audience: Audience; doc_type: DocType; label: string }[] = [
    ...SHARED_TYPES.map((d) => ({ audience: "rider" as Audience, ...d })),
    ...SHARED_TYPES.map((d) => ({ audience: "driver" as Audience, ...d })),
    ...DRIVER_ONLY_TYPES.map((d) => ({ audience: "driver" as Audience, ...d })),
];

const A_CFG: Record<Audience, { l: string; c: string }> = {
    rider: { l: "Rider", c: "bg-sky-500/15 text-sky-600" },
    driver: { l: "Driver", c: "bg-emerald-500/15 text-emerald-600" },
};

export default function LegalDocumentsTab() {
    const { toast } = useToast();
    const [rows, setRows] = useState<Record<string, Row>>({});
    const [drafts, setDrafts] = useState<Record<string, string>>({});
    const [loading, setLoading] = useState(true);
    const [savingKey, setSavingKey] = useState<string | null>(null);
    const [savedKey, setSavedKey] = useState<string | null>(null);
    // 9 doc types × 2 audiences (17 combos incl. the driver-only appeals
    // policy) is too many to lay out as a flat TabsList — audience is the
    // outer tab, doc type is a dropdown scoped to that audience's list.
    const [docTypeByAudience, setDocTypeByAudience] = useState<Record<Audience, DocType>>({
        rider: "tos",
        driver: "tos",
    });
    const reqIdRef = useRef(0);

    const keyOf = (a: Audience, t: DocType) => `${a}/${t}`;

    const load = useCallback(() => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        getLegalDocuments()
            .then((data) => {
                if (reqId !== reqIdRef.current) return;
                const next: Record<string, Row> = {};
                const nextDrafts: Record<string, string> = {};
                (Array.isArray(data) ? data : []).forEach((r: any) => {
                    const k = keyOf(r.audience, r.doc_type);
                    next[k] = r;
                    nextDrafts[k] = r.content || "";
                });
                // Seed empty drafts for any missing combo so the textarea is editable.
                DOCS.forEach((d) => {
                    const k = keyOf(d.audience, d.doc_type);
                    if (!(k in nextDrafts)) nextDrafts[k] = "";
                });
                setRows(next);
                setDrafts(nextDrafts);
            })
            .catch(() => {
                if (reqId === reqIdRef.current) {
                    setRows({});
                    const empty: Record<string, string> = {};
                    DOCS.forEach((d) => { empty[keyOf(d.audience, d.doc_type)] = ""; });
                    setDrafts(empty);
                }
            })
            .finally(() => { if (reqId === reqIdRef.current) setLoading(false); });
    }, []);
    useEffect(() => { load(); }, [load]);

    const save = async (audience: Audience, doc_type: DocType) => {
        const k = keyOf(audience, doc_type);
        const content = drafts[k] ?? "";
        setSavingKey(k);
        setSavedKey(null);
        try {
            await upsertLegalDocument({ audience, type: doc_type, content });
            setSavedKey(k);
            setTimeout(() => setSavedKey((cur) => (cur === k ? null : cur)), 1800);
            toast({ title: "Legal document saved" });
            load();
        } catch (e: any) {
            toast({ title: "Failed to save document", description: e?.message, variant: "destructive" });
        } finally {
            setSavingKey(null);
        }
    };

    if (loading) {
        return (
            <div className="flex items-center justify-center py-20">
                <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
        );
    }

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <p className="text-xs sm:text-sm text-muted-foreground">
                    Edit per-audience legal and policy pages. Changes take effect immediately in the mobile apps —
                    publish only content that has cleared counsel review (see docs/legal/legal-text-publication-checklist.md).
                </p>
                <Button variant="outline" size="sm" onClick={load}>
                    <RefreshCw className="h-3.5 w-3.5 mr-1.5" /> Refresh
                </Button>
            </div>

            <Tabs defaultValue="rider" className="w-full">
                <TabsList className="grid grid-cols-2 w-full max-w-xs">
                    <TabsTrigger value="rider">Rider</TabsTrigger>
                    <TabsTrigger value="driver">Driver</TabsTrigger>
                </TabsList>
                {(["rider", "driver"] as Audience[]).map((audience) => {
                    const typesForAudience =
                        audience === "driver" ? [...SHARED_TYPES, ...DRIVER_ONLY_TYPES] : SHARED_TYPES;
                    const docType = docTypeByAudience[audience];
                    const activeDef = typesForAudience.find((t) => t.doc_type === docType) ?? typesForAudience[0];
                    const k = keyOf(audience, activeDef.doc_type);
                    const row = rows[k];
                    const draft = drafts[k] ?? "";
                    const original = row?.content || "";
                    const dirty = draft !== original;
                    return (
                        <TabsContent key={audience} value={audience} className="mt-4 space-y-3">
                            <Select
                                value={activeDef.doc_type}
                                onValueChange={(v) =>
                                    setDocTypeByAudience((cur) => ({ ...cur, [audience]: v as DocType }))
                                }
                            >
                                <SelectTrigger className="w-full sm:w-80">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    {typesForAudience.map((t) => (
                                        <SelectItem key={t.doc_type} value={t.doc_type}>
                                            {t.label}
                                        </SelectItem>
                                    ))}
                                </SelectContent>
                            </Select>

                            <Card>
                                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
                                    <div>
                                        <CardTitle className="text-base">{activeDef.label}</CardTitle>
                                        <div className="flex items-center gap-2 mt-2">
                                            <Badge className={A_CFG[audience].c}>{A_CFG[audience].l}</Badge>
                                            {row?.version != null && (
                                                <Badge variant="outline">v{row.version}</Badge>
                                            )}
                                            {row?.updated_at && (
                                                <span className="text-xs text-muted-foreground">
                                                    Updated {formatDate(row.updated_at)}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                    <Button
                                        size="sm"
                                        disabled={savingKey === k || !dirty}
                                        onClick={() => save(audience, activeDef.doc_type)}
                                    >
                                        {savedKey === k ? (
                                            <><Check className="h-3.5 w-3.5 mr-1.5" /> Saved</>
                                        ) : (
                                            <><Save className="h-3.5 w-3.5 mr-1.5" /> {savingKey === k ? "Saving..." : "Save"}</>
                                        )}
                                    </Button>
                                </CardHeader>
                                <CardContent>
                                    <Textarea
                                        value={draft}
                                        onChange={(e) => setDrafts((cur) => ({ ...cur, [k]: e.target.value }))}
                                        placeholder={`Enter ${activeDef.label} content...`}
                                        className="min-h-[420px] font-mono text-sm"
                                    />
                                    <p className="text-xs text-muted-foreground mt-2">
                                        {draft.length.toLocaleString()} characters
                                        {dirty && <span className="ml-2 text-amber-600 dark:text-amber-400">· unsaved changes</span>}
                                    </p>
                                </CardContent>
                            </Card>
                        </TabsContent>
                    );
                })}
            </Tabs>
        </div>
    );
}
