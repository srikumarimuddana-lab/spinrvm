"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { getLegalDocuments, upsertLegalDocument } from "@/lib/api";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { RefreshCw, Save, Check } from "lucide-react";
import { formatDate } from "@/lib/utils";
import { useToast } from "@/components/ui/use-toast";

type Audience = "rider" | "driver";
type DocType = "tos" | "privacy";

type Row = {
    id?: string;
    audience: Audience;
    doc_type: DocType;
    content: string;
    version?: number;
    updated_at?: string;
};

const DOCS: { audience: Audience; doc_type: DocType; label: string }[] = [
    { audience: "rider", doc_type: "tos", label: "Rider · Terms of Service" },
    { audience: "rider", doc_type: "privacy", label: "Rider · Privacy Policy" },
    { audience: "driver", doc_type: "tos", label: "Driver · Terms of Service" },
    { audience: "driver", doc_type: "privacy", label: "Driver · Privacy Policy" },
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
                    Edit per-audience Terms of Service and Privacy Policy. Changes take effect immediately in mobile apps.
                </p>
                <Button variant="outline" size="sm" onClick={load}>
                    <RefreshCw className="h-3.5 w-3.5 mr-1.5" /> Refresh
                </Button>
            </div>

            <Tabs defaultValue="rider/tos" className="w-full">
                <TabsList className="grid grid-cols-2 sm:grid-cols-4 w-full">
                    {DOCS.map((d) => (
                        <TabsTrigger key={keyOf(d.audience, d.doc_type)} value={keyOf(d.audience, d.doc_type)}>
                            {d.label}
                        </TabsTrigger>
                    ))}
                </TabsList>
                {DOCS.map((d) => {
                    const k = keyOf(d.audience, d.doc_type);
                    const row = rows[k];
                    const draft = drafts[k] ?? "";
                    const original = row?.content || "";
                    const dirty = draft !== original;
                    return (
                        <TabsContent key={k} value={k} className="mt-4">
                            <Card>
                                <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-3">
                                    <div>
                                        <CardTitle className="text-base">{d.label}</CardTitle>
                                        <div className="flex items-center gap-2 mt-2">
                                            <Badge className={A_CFG[d.audience].c}>{A_CFG[d.audience].l}</Badge>
                                            <Badge variant="outline">{d.doc_type === "tos" ? "Terms of Service" : "Privacy Policy"}</Badge>
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
                                        onClick={() => save(d.audience, d.doc_type)}
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
                                        placeholder={`Enter ${d.label} content...`}
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
