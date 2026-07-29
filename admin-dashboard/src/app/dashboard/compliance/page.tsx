"use client";

import { useState } from "react";
import { Download, Loader2, FileText, Mail } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/use-toast";
import { InfoHint as Hint } from "@/components/info-hint";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useAuthStore } from "@/store/authStore";
import {
    downloadGstPstRemittance,
    downloadInsurancePeriodAudit,
    downloadKnightArcherDriverOnboarding,
    downloadT4aFilerHandoff,
    emailGstPstRemittance,
    emailInsurancePeriodAudit,
    emailKnightArcherDriverOnboarding,
    type ComplianceReportFormat,
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

const DRIVER_STATUSES = ["active", "pending", "needs_review", "suspended", "banned"];

/** Inline "email to @spinr.ca" control shared by all three report cards —
 * a small text input + send button next to the existing Download button,
 * not a separate flow, so it doesn't require re-selecting the report's
 * filters. */
function EmailReportControl({ onSend, loading }: { onSend: (email: string) => Promise<void>; loading: boolean }) {
    const [email, setEmail] = useState("");
    const valid = /^[^\s@]+@spinr\.ca$/i.test(email.trim());
    return (
        <div className="flex items-center gap-1.5">
            <Input
                placeholder="name@spinr.ca"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="w-48"
            />
            <Button variant="outline" disabled={!valid || loading} onClick={() => onSend(email.trim())}>
                {loading ? <Loader2 className="h-4 w-4 mr-2 animate-spin" /> : <Mail className="h-4 w-4 mr-2" />}
                Email
            </Button>
            <Hint text="Reports can only be emailed to a @spinr.ca address — this sends the same report currently configured above instead of downloading it." />
        </div>
    );
}

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
    const [gstPstEmailLoading, setGstPstEmailLoading] = useState(false);

    const [auditRange, setAuditRange] = useState("30d");
    const [auditFormat, setAuditFormat] = useState<ComplianceReportFormat>("pdf");
    const [auditDriverId, setAuditDriverId] = useState("");
    const [auditLoading, setAuditLoading] = useState(false);
    const [auditEmailLoading, setAuditEmailLoading] = useState(false);

    const [kaFormat, setKaFormat] = useState<ComplianceReportFormat>("pdf");
    const [kaStatus, setKaStatus] = useState("all");
    const [kaLoading, setKaLoading] = useState(false);
    const [kaEmailLoading, setKaEmailLoading] = useState(false);

    const [t4aYear, setT4aYear] = useState(new Date().getFullYear() - 1);
    const [t4aFormat, setT4aFormat] = useState<ComplianceReportFormat>("xlsx");
    const [t4aLoading, setT4aLoading] = useState(false);
    const isSuperAdmin = useAuthStore((s) => s.user?.role === "super_admin");

    if (!allowed) return null;

    // `onEmailError` used the same "Could not generate report" title as
    // download failures, which is misleading when the report generated
    // fine and only the send step failed (e.g. SES/Resend misconfigured) —
    // an admin reading "generate" would assume the report itself is broken
    // and go looking in the wrong place.
    const onError = (e: any) =>
        toast({ title: "Could not generate report", description: e?.message || "Unknown error", variant: "destructive" });
    const onEmailError = (e: any) =>
        toast({ title: "Could not email report", description: e?.message || "Unknown error", variant: "destructive" });
    const onEmailed = (email: string) => toast({ title: "Report sent", description: `Emailed to ${email}.` });

    const onDownloadGstPst = async () => {
        setGstPstLoading(true);
        try {
            const { blob, filename } = await downloadGstPstRemittance(gstPstRange, gstPstFormat);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setGstPstLoading(false);
        }
    };
    const onEmailGstPst = async (email: string) => {
        setGstPstEmailLoading(true);
        try {
            await emailGstPstRemittance(gstPstRange, gstPstFormat, email);
            onEmailed(email);
        } catch (e: any) {
            onEmailError(e);
        } finally {
            setGstPstEmailLoading(false);
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
            onError(e);
        } finally {
            setAuditLoading(false);
        }
    };
    const onEmailAudit = async (email: string) => {
        setAuditEmailLoading(true);
        try {
            await emailInsurancePeriodAudit(auditRange, auditFormat, email, auditDriverId.trim() || undefined);
            onEmailed(email);
        } catch (e: any) {
            onEmailError(e);
        } finally {
            setAuditEmailLoading(false);
        }
    };

    const onDownloadKa = async () => {
        setKaLoading(true);
        try {
            const { blob, filename } = await downloadKnightArcherDriverOnboarding(
                kaFormat,
                kaStatus === "all" ? undefined : kaStatus,
            );
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setKaLoading(false);
        }
    };
    const onEmailKa = async (email: string) => {
        setKaEmailLoading(true);
        try {
            await emailKnightArcherDriverOnboarding(kaFormat, email, kaStatus === "all" ? undefined : kaStatus);
            onEmailed(email);
        } catch (e: any) {
            onEmailError(e);
        } finally {
            setKaEmailLoading(false);
        }
    };

    const onDownloadT4a = async () => {
        setT4aLoading(true);
        try {
            const { blob, filename } = await downloadT4aFilerHandoff(t4aYear, t4aFormat);
            triggerBrowserDownload(blob, filename);
        } catch (e: any) {
            onError(e);
        } finally {
            setT4aLoading(false);
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
                    <TabsTrigger value="knight-archer">Knight Archer Driver Onboarding</TabsTrigger>
                    {isSuperAdmin && <TabsTrigger value="t4a-filer">T4A Filer Handoff</TabsTrigger>}
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
                                <EmailReportControl onSend={onEmailGstPst} loading={gstPstEmailLoading} />
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
                                <EmailReportControl onSend={onEmailAudit} loading={auditEmailLoading} />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                <TabsContent value="knight-archer">
                    <Card>
                        <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                                <FileText className="h-5 w-5" />
                                Knight Archer Insurance — Driver Onboarding
                            </CardTitle>
                            <CardDescription className="flex items-start gap-1.5">
                                <span>
                                    Driver name, license number, license class, and current status for every
                                    onboarded driver — sent to Knight Archer Insurance. Includes drivers of
                                    every status by default (not just active) unless you filter below.
                                </span>
                                <Hint text="Knight Archer needs the full roster including pending/needs_review/suspended/banned drivers, not just active ones — that's why 'All statuses' is the default here." />
                            </CardDescription>
                        </CardHeader>
                        <CardContent className="space-y-4">
                            <div className="flex flex-wrap items-end gap-4">
                                <div className="space-y-1.5">
                                    <Label>Status</Label>
                                    <Select value={kaStatus} onValueChange={setKaStatus}>
                                        <SelectTrigger className="w-44">
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value="all">All statuses</SelectItem>
                                            {DRIVER_STATUSES.map((s) => (
                                                <SelectItem key={s} value={s}>{s.replace(/_/g, " ")}</SelectItem>
                                            ))}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <Label>Format</Label>
                                    <Select value={kaFormat} onValueChange={(v) => setKaFormat(v as ComplianceReportFormat)}>
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
                                <Button onClick={onDownloadKa} disabled={kaLoading}>
                                    {kaLoading ? (
                                        <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                    ) : (
                                        <Download className="h-4 w-4 mr-2" />
                                    )}
                                    Download
                                </Button>
                                <EmailReportControl onSend={onEmailKa} loading={kaEmailLoading} />
                            </div>
                        </CardContent>
                    </Card>
                </TabsContent>

                {isSuperAdmin && (
                    <TabsContent value="t4a-filer">
                        <Card>
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2">
                                    <FileText className="h-5 w-5" />
                                    T4A Filer Handoff
                                </CardTitle>
                                <CardDescription className="flex items-start gap-1.5">
                                    <span>
                                        Per-driver annual earnings plus Stripe-verified legal name and mailing
                                        address, for every driver at or above the $500 CRA reporting threshold —
                                        for handoff to a third-party tax filer (accountant or platform-reporting
                                        vendor). Never includes the SIN itself: SIN stays on Stripe&apos;s side and
                                        is retrieved per-driver only via the audited &quot;Reveal SIN&quot; action on a
                                        driver&apos;s Payouts tab when actually filing.
                                    </span>
                                    <Hint text="Two CRA obligations can apply to Spinr: the traditional T4A (Box 048, $500 threshold) and the newer Reportable Platform Operator rules (Part XX.1, since Jan 2024). Which apply, and whether they overlap, is a determination for your tax filer — this export gives them everything except the SIN." />
                                </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                                <div className="flex flex-wrap items-end gap-4">
                                    <div className="space-y-1.5">
                                        <Label>Tax year</Label>
                                        <Input
                                            type="number"
                                            className="w-28"
                                            value={t4aYear}
                                            onChange={(e) => setT4aYear(Number(e.target.value) || t4aYear)}
                                            min={2020}
                                            max={2100}
                                        />
                                    </div>
                                    <div className="space-y-1.5">
                                        <Label>Format</Label>
                                        <Select value={t4aFormat} onValueChange={(v) => setT4aFormat(v as ComplianceReportFormat)}>
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
                                    <Button onClick={onDownloadT4a} disabled={t4aLoading}>
                                        {t4aLoading ? (
                                            <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                                        ) : (
                                            <Download className="h-4 w-4 mr-2" />
                                        )}
                                        Download
                                    </Button>
                                </div>
                                <p className="text-xs text-muted-foreground">
                                    Super admin only — this export surfaces verified legal name and mailing
                                    address in bulk, more sensitive than the aggregate totals in the other
                                    reports on this page. No email option; download and hand off through your
                                    own secure channel to the filer.
                                </p>
                            </CardContent>
                        </Card>
                    </TabsContent>
                )}
            </Tabs>
        </div>
    );
}
