// Documents tab of the drivers detail slideout. Pure code motion out of
// drivers/page.tsx (design-audit follow-up, PR #4955's un-extracted
// remainder) -- no logic changes. Controlled/presentational, matching
// driver-payouts-tab.tsx. `selected`/`activeDocs`/`requiredDocs` stay `any`
// to match the existing driver-documents-helpers.tsx signatures they're
// passed into (DocCard, DocExpirySummaryCard, matchesRequirement).
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Upload, Maximize2, FileText, Image } from "lucide-react";
import { matchesRequirement, DocExpirySummaryCard, DocCard } from "./driver-documents-helpers";

interface DocSummary {
    expiry?: string;
    docStatus: "approved" | "pending" | "rejected" | "missing";
    expiryIsLegacy: boolean;
}

interface RequiredDoc {
    id?: string;
    key: string;
    label: string;
    has_expiry: boolean;
}

interface DriverDocumentsTabProps {
    selected: any;
    requiredDocs: RequiredDoc[];
    activeDocs: any[];
    docsLoading: boolean;
    docBusy: string | null;
    canReviewDocuments: boolean;
    setUploadDialogOpen: (open: boolean) => void;
    setOpenReviewerForDriver: (v: { id: string; name: string }) => void;
    openReviewDialog: (docId: string, action: "approved" | "rejected") => void;
    setPreviewUrl: (url: string | null) => void;
    getDocSummary: (rdId: string | undefined, rdKey: string, rdLabel: string) => DocSummary;
}

export default function DriverDocumentsTab({
    selected,
    requiredDocs,
    activeDocs,
    docsLoading,
    docBusy,
    canReviewDocuments,
    setUploadDialogOpen,
    setOpenReviewerForDriver,
    openReviewDialog,
    setPreviewUrl,
    getDocSummary,
}: DriverDocumentsTabProps) {
    return (
        <>
                                    <div className="flex items-center justify-between gap-2 -mt-1">
                                        <p className="text-xs text-muted-foreground">
                                            Review docs inline below, or open the full-screen reviewer for keyboard-driven triage.
                                        </p>
                                        <div className="flex items-center gap-2">
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8"
                                                onClick={() => setUploadDialogOpen(true)}
                                            >
                                                <Upload className="h-3.5 w-3.5 mr-1.5" />
                                                Upload Document
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="outline"
                                                className="h-8"
                                                disabled={!canReviewDocuments}
                                                title={canReviewDocuments ? undefined : "Requires the \"Documents\" module — ask an admin to grant it"}
                                                onClick={() => setOpenReviewerForDriver({ id: selected.id, name: selected.name || selected.email || selected.id })}
                                            >
                                                <Maximize2 className="h-3.5 w-3.5 mr-1.5" />
                                                Open in Reviewer
                                            </Button>
                                        </div>
                                    </div>
                                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                                        {docsLoading ? (
                                            <>{[1,2,3,4,5].map(i => <div key={i} className="h-24 bg-muted rounded-xl animate-pulse" />)}</>
                                        ) : requiredDocs.length > 0 ? requiredDocs.filter(rd => rd.has_expiry).map(rd => (
                                            <DocExpirySummaryCard
                                                key={rd.key}
                                                label={rd.label}
                                                summary={getDocSummary(rd.id, rd.key, rd.label)}
                                            />
                                        )) : (<>
                                            <DocExpirySummaryCard label="Driver's License"     summary={getDocSummary(undefined, "drivers_license",      "Driver's License")} />
                                            <DocExpirySummaryCard label="Vehicle Insurance"    summary={getDocSummary(undefined, "vehicle_insurance",    "Vehicle Insurance")} />
                                            <DocExpirySummaryCard label="Vehicle Registration" summary={getDocSummary(undefined, "vehicle_registration", "Vehicle Registration")} />
                                            <DocExpirySummaryCard label="Vehicle Inspection"   summary={getDocSummary(undefined, "vehicle_inspection",  "Vehicle Inspection")} />
                                            <DocExpirySummaryCard label="Background Check"     summary={getDocSummary(undefined, "background_check",    "Background Check")} />
                                        </>)}
                                    </div>
                                    {docsLoading ? <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">{[1,2,3,4].map(i=><div key={i} className="h-48 bg-muted rounded-xl animate-pulse" />)}</div>
                                    : requiredDocs.length > 0 ? (
                                        <div className="space-y-6">
                                            {requiredDocs.map(reqDoc => {
                                                const matchingDocs = activeDocs.filter(d => matchesRequirement(d, reqDoc));
                                                const counts = {
                                                    pending: matchingDocs.filter(d => d.status === "pending").length,
                                                    approved: matchingDocs.filter(d => d.status === "approved").length,
                                                    rejected: matchingDocs.filter(d => d.status === "rejected").length,
                                                };
                                                // Surface the expiry gap directly in the section header
                                                // when the requirement needs an expiry but the approved
                                                // doc has none on file — the previous "Requires Expiry"
                                                // pill was static and read as a warning even after a
                                                // valid approval, confusing reviewers.
                                                const summary = getDocSummary(reqDoc.id, reqDoc.key, reqDoc.label);
                                                const expiryMissing = reqDoc.has_expiry && summary.docStatus === "approved" && !summary.expiry;
                                                return (
                                                    <div key={reqDoc.key}>
                                                        <div className="flex items-center gap-2 mb-3 flex-wrap">
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h3 className="text-sm font-semibold">{reqDoc.label}</h3>
                                                            {matchingDocs.length === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Missing</Badge>}
                                                            {counts.pending > 0 && <Badge className="bg-warning/15 text-warning text-[10px]">{counts.pending} pending</Badge>}
                                                            {counts.approved > 0 && counts.pending === 0 && !expiryMissing && <Badge className="bg-success/15 text-success text-[10px]">Approved</Badge>}
                                                            {expiryMissing && counts.pending === 0 && <Badge className="bg-warning/15 text-warning text-[10px]">Approved · expiry not recorded</Badge>}
                                                            {counts.rejected > 0 && counts.pending === 0 && counts.approved === 0 && <Badge className="bg-destructive/15 text-destructive text-[10px]">Re-upload needed</Badge>}
                                                        </div>
                                                        {matchingDocs.length > 0 ? <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">{matchingDocs.map(d=><DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}</div>
                                                        : <div className="bg-muted/20 border border-dashed rounded-xl p-6 text-center text-muted-foreground"><Image className="h-8 w-8 mx-auto mb-2 opacity-20" /><p className="text-sm">No {reqDoc.label} uploaded yet</p></div>}
                                                    </div>
                                                );
                                            })}
                                            {/* Other Documents: any active docs not matched by any required doc */}
                                            {(() => {
                                                const matchedIds = new Set(requiredDocs.flatMap(reqDoc =>
                                                    activeDocs.filter(d => matchesRequirement(d, reqDoc)).map(d => d.id)
                                                ));
                                                const unmatched = activeDocs.filter(d => !matchedIds.has(d.id));
                                                if (unmatched.length === 0) return null;
                                                return (
                                                    <div>
                                                        <div className="flex items-center gap-2 mb-3">
                                                            <FileText className="h-4 w-4 text-muted-foreground" /><h3 className="text-sm font-semibold">Other Documents</h3>
                                                            <Badge variant="outline" className="text-[10px]">{unmatched.length} uploaded</Badge>
                                                        </div>
                                                        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">{unmatched.map(d=><DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}</div>
                                                    </div>
                                                );
                                            })()}
                                        </div>
                                    ) : activeDocs.length > 0 ? (
                                        <div className="space-y-3">
                                            <p className="text-xs text-muted-foreground">No service area configured — showing all uploaded documents</p>
                                            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                                                {activeDocs.map(d => <DocCard key={d.id} d={d} docBusy={docBusy} driverName={selected?.name || selected?.full_name || ''} onPreview={setPreviewUrl} onReview={openReviewDialog} />)}
                                            </div>
                                        </div>
                                    ) : (
                                        <div className="text-center py-12 text-muted-foreground bg-muted/20 rounded-xl border border-dashed"><Image className="h-10 w-10 mx-auto mb-3 opacity-30" /><p className="text-sm font-medium">No document requirements configured for this service area</p></div>
                                    )}
        </>
    );
}
