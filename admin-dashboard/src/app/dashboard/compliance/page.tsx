"use client";

import { useState } from "react";
import { Download, Loader2, FileText } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import {
    downloadGstPstRemittance, downloadInsurancePeriodAudit, type ComplianceReportFormat,
} from "@/lib/api";

const DATE_RANGES = [
    { value: "today", label: "Today" },
    { value: "7d", label: "7 Days" },
    { value: "30d", label: "30 Days" },
    { value: "90d", label: "90 Days" },
    { value: "1y", label: "1 Year" },
];

const FORMATS: { value: ComplianceReportFormat; label: string }[] = [
    { value: "pdf", label: "PDF" },
    { value: "csv", label: "CSV" },
    { value: "xlsx", label: "Excel" },
    { value: "docx", label: "Word" },
];

function triggerBrowserDownload(blob: Blob, filename: string) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

/**
 * Compliance & Tax Reporting — regulatory/tax exports that aren't covered
 * by operational analytics (/dashboard/analytics) or the fixed-format SGI
 * forms (Data Transfer tab). Every report here is Spinr-branded and logged
 * to compliance_export_events server-side.
 */
export default function CompliancePage() {
    const { allowed } = useRequireModule("compliance");
    const { toast } = useToast();

    const [gstPstRange, setGstPstRange] = useState("30d");
    const [gstPstFormat, setGstPstFormat] = useState<ComplianceReportFormat>("pdf");
    const [gstPstLoading, setGstPstLoading] = useState(false);

    const [auditRange, setAuditRange] = useState("30d");
    const [auditFormat, setAuditFormat] = useState<ComplianceReportFormat>("pdf");
    const [auditDriverId, setAuditDriverId] = useState("");
    const [auditLoading, setAuditLoading] = useState(false);

    if (!allowed) return null;

    const onDownloadGstPst = async () => {
        setGstPstLoading(true);
        try {
            const { blob, filename } = await downloadGstPstRemittance(gstPstRange, gstPstFormat);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            toast({
                title: "Could not generate report",
                description: e?.message || "Unknown error",
                variant: "destructive",
            });
        } finally {
            setGstPstLoading(false);
        }
    };

    const onDownloadAudit = async () => {
        setAuditLoading(true);
        try {
            const { blob, filename } = await downloadInsurancePeriodAudit(
                auditRange,
                auditFormat,
                auditDriverId.trim() || undefined,
            );
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            toast({
                title: "Could not generate report",
                description: e?.message || "Unknown error",
                variant: "destructive",
            });
        } finally {
            setAuditLoading(false);
        }
    };

    return (
        <div className="p-6 space-y-6">
            <div>
                <h1 className="text-2xl font-semibold">Compliance & Tax Reporting</h1>
                <p className="text-muted-foreground">
                    Spinr-branded regulatory and tax exports — GST/PST remittance and insurance-period
                    audit trails. Every export here is logged with the requesting admin, date range, and
                    row count for a future privacy/regulatory audit. Fixed-format regulator documents
                    (SGI D00032/D00033) live under Data Transfer instead — this module never re-styles
                    those into Spinr branding.
                </p>
            </div>

            <Tabs defaultValue="gst-pst">
                <TabsList>
                    <TabsTrigger value="gst-pst">GST/PST Remittance</TabsTrigger>
                    <TabsTrigger value="insurance-audit">Insurance-Period Audit</TabsTrigger>
                </TabsList>

                <TabsContent value="gst-pst">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                GST/PST Remittance Summary
                            </CardTitle>
                            <CardDescription>
                                GST/PST/HST collected on completed rides, grouped by month, summed from what
                                was actually charged on each ride&apos;s receipt — never recomputed.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <div className="space-y-1.5">
                                    <Label>Date range</Label>
                                    <Select value={gstPstRange} onValueChange={setGstPstRange}>
                                        <SelectTrigger className="w-40">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {DATE_RANGES.map((r) => (
                                                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select
                                        value={gstPstFormat}
                                        onValueChange={(v) => setGstPstFormat(v as ComplianceReportFormat)}
                                    >
                                        <SelectTrigger className="w-32">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadGstPst} disabled={gstPstLoading}>
                                    {gstPstLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="insurance-audit">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                Driver Insurance-Period Audit
                            </CardTitle>
                            <CardDescription>
                                Every driver insurance-period transition (offline / available / en-route /
                                passenger-aboard) in the requested window — for an SGI or future province&apos;s
                                regulator audit of TNC insurance-period classification. Leave driver ID blank
                                for all drivers.
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <div className="space-y-1.5">
                                    <Label>Date range</Label>
                                    <Select value={auditRange} onValueChange={setAuditRange}>
                                        <SelectTrigger className="w-40">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {DATE_RANGES.map((r) => (
                                                <SelectItem key={r.value} value={r.value}>{r.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <Label>Driver ID (optional)</Label>
                                    <Input
                                        className="w-56"
                                        placeholder="All drivers"
                                        value={auditDriverId}
                                        onChange={(e) => setAuditDriverId(e.target.value)}
                                    />
                                </div>
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select
                                        value={auditFormat}
                                        onValueChange={(v) => setAuditFormat(v as ComplianceReportFormat)}
                                    >
                                        <SelectTrigger className="w-32">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {FORMATS.map((f) => (
                                                <SelectItem key={f.value} value={f.value}>{f.label}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <Button onClick={onDownloadAudit} disabled={auditLoading}>
                                    {auditLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>
            </Tabs>
        </div>
    );
}
