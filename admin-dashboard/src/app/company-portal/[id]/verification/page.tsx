"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
    CheckCircle2,
    Clock,
    FileUp,
    Loader2,
    ShieldAlert,
    ShieldX,
    Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    CompanyKybState,
    getCompanyKyb,
    getKybUploadUrl,
    submitKybDocument,
    uploadKybFile,
} from "@/lib/companyApi";

/**
 * Company verification (KYB) page — M2.5.
 *
 * The landing page for every non-active company (the portal layout redirects
 * here, M2.6). Admins upload a business document (PDF/PNG/JPEG) into the
 * private kyb-documents bucket via a short-lived signed URL, then confirm the
 * submission; Spinr staff review it in the KYB queue. A rejection shows the
 * reviewer's note and allows resubmission (which re-enters the queue).
 */

const ACCEPTED = "application/pdf,image/png,image/jpeg";
const MAX_BYTES = 10 * 1024 * 1024; // keep well under any bucket limit

export default function VerificationPage() {
    const { id } = useParams<{ id: string }>();
    const [kyb, setKyb] = useState<CompanyKybState | null>(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);
    const fileRef = useRef<HTMLInputElement>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        setError(null);
        try {
            setKyb(await getCompanyKyb(id));
        } catch (e) {
            // Members don't have access to /kyb (admin-only) — show the
            // generic pending message instead of an error wall.
            setKyb(null);
            if (!(e instanceof Error && /403|forbidden|admin/i.test(e.message))) {
                setError(e instanceof Error ? e.message : "Failed to load verification state");
            }
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const onPickFile = () => fileRef.current?.click();

    const onFileChosen = async (file: File | null) => {
        if (!id || !file) return;
        setError(null);
        setNotice(null);
        if (!ACCEPTED.split(",").includes(file.type)) {
            setError("Document must be a PDF, PNG, or JPEG.");
            return;
        }
        if (file.size > MAX_BYTES) {
            setError("Document must be under 10 MB.");
            return;
        }
        setBusy(true);
        try {
            const { signed_url, path } = await getKybUploadUrl(id, file.type);
            await uploadKybFile(signed_url, file);
            const result = await submitKybDocument(id, path);
            setNotice(
                result.resubmitted
                    ? "Document resubmitted — your application is back under review."
                    : "Document submitted — our team will review it shortly."
            );
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Upload failed — please try again.");
        } finally {
            setBusy(false);
            if (fileRef.current) fileRef.current.value = "";
        }
    };

    const state = kyb?.state;

    return (
        <div className="mx-auto max-w-2xl space-y-6">
            <div>
                <h1 className="text-2xl font-bold tracking-tight">Account verification</h1>
                <p className="text-muted-foreground mt-1 text-sm">
                    Spinr reviews every business before rides can be booked.
                </p>
            </div>

            {loading ? (
                <Card>
                    <CardContent className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
                        <Loader2 className="h-4 w-4 animate-spin" /> Loading verification status…
                    </CardContent>
                </Card>
            ) : !kyb ? (
                <Card>
                    <CardContent className="flex items-center gap-3 p-6 text-sm">
                        <Clock className="h-5 w-5 text-amber-500" />
                        Your company is pending approval. A company admin handles the
                        verification documents — check back once it&apos;s approved.
                    </CardContent>
                </Card>
            ) : (
                <>
                    {state === "approved" && (
                        <Card>
                            <CardContent className="flex items-center gap-3 p-6 text-sm">
                                <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                                Your company is verified and active — you&apos;re all set.
                            </CardContent>
                        </Card>
                    )}

                    {state === "suspended" && (
                        <Card>
                            <CardContent className="flex items-center gap-3 p-6 text-sm">
                                <ShieldAlert className="h-5 w-5 text-red-600" />
                                Your account has been suspended by Spinr. Contact support to
                                resolve this — documents cannot be resubmitted from here.
                            </CardContent>
                        </Card>
                    )}

                    {state === "closed" && (
                        <Card>
                            <CardContent className="flex items-center gap-3 p-6 text-sm">
                                <ShieldX className="h-5 w-5 text-muted-foreground" />
                                This account is closed.
                            </CardContent>
                        </Card>
                    )}

                    {state === "rejected" && (
                        <Card className="border-red-200">
                            <CardHeader>
                                <CardTitle className="flex items-center gap-2 text-base">
                                    <ShieldAlert className="h-5 w-5 text-red-600" />
                                    Verification needs attention
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3 text-sm">
                                <p>We couldn&apos;t approve your documents yet.</p>
                                {kyb.review_note && (
                                    <p className="rounded bg-destructive/10 p-3 text-destructive">
                                        Reviewer note: {kyb.review_note}
                                    </p>
                                )}
                                <p>Fix the issue and resubmit — you&apos;ll re-enter the review queue.</p>
                            </CardContent>
                        </Card>
                    )}

                    {state === "under_review" && (
                        <Card>
                            <CardContent className="flex items-center gap-3 p-6 text-sm">
                                <Clock className="h-5 w-5 text-amber-500" />
                                <span>
                                    Your document is under review
                                    {kyb.submitted_at &&
                                        ` (submitted ${new Date(kyb.submitted_at).toLocaleDateString()})`}
                                    . We&apos;ll email you the decision.
                                </span>
                            </CardContent>
                        </Card>
                    )}

                    {state === "not_submitted" && (
                        <Card>
                            <CardContent className="flex items-center gap-3 p-6 text-sm">
                                <FileUp className="h-5 w-5 text-muted-foreground" />
                                Upload a business document to start verification — articles of
                                incorporation, GST/HST registration, or a CRA business-number
                                confirmation.
                            </CardContent>
                        </Card>
                    )}

                    {kyb.can_resubmit && (
                        <Card>
                            <CardHeader>
                                <CardTitle className="text-base">
                                    {state === "rejected"
                                        ? "Resubmit your document"
                                        : state === "under_review"
                                          ? "Replace the submitted document"
                                          : "Upload your document"}
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="space-y-3">
                                <input
                                    ref={fileRef}
                                    type="file"
                                    accept={ACCEPTED}
                                    className="hidden"
                                    onChange={(e) => onFileChosen(e.target.files?.[0] ?? null)}
                                />
                                <Button onClick={onPickFile} disabled={busy} className="w-full">
                                    {busy ? (
                                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    ) : (
                                        <Upload className="mr-2 h-4 w-4" />
                                    )}
                                    {busy ? "Uploading…" : "Choose a PDF, PNG, or JPEG"}
                                </Button>
                                <p className="text-xs text-muted-foreground">
                                    Documents are stored privately and only visible to Spinr&apos;s
                                    verification team.
                                </p>
                            </CardContent>
                        </Card>
                    )}

                    {notice && (
                        <p className="rounded bg-emerald-50 dark:bg-emerald-900/20 p-3 text-sm text-emerald-800 dark:text-emerald-300">{notice}</p>
                    )}
                </>
            )}

            {error && <p className="rounded bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
        </div>
    );
}
