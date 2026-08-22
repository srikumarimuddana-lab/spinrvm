"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, XCircle, Clock, Loader2, Download, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Table, TableHeader, TableBody, TableHead, TableRow, TableCell } from "@/components/ui/table";
import { useToast } from "@/components/ui/use-toast";
import { listDataTransferJobs, regenerateDataTransferJobDownload, type DataTransferJob } from "@/lib/api";

function StatusIcon({ status }: { status: DataTransferJob["status"] }) {
    if (status === "completed") return <CheckCircle2 className="h-4 w-4 text-success" />;
    if (status === "failed") return <XCircle className="h-4 w-4 text-destructive" />;
    return <Clock className="h-4 w-4 text-muted-foreground" />;
}

function isExpired(job: DataTransferJob): boolean {
    if (!job.expires_at) return false;
    return new Date(job.expires_at).getTime() < Date.now();
}

export function JobsTab() {
    const { toast } = useToast();
    const [jobs, setJobs] = useState<DataTransferJob[]>([]);
    const [loading, setLoading] = useState(false);
    const [downloadingId, setDownloadingId] = useState<string | null>(null);

    const load = async () => {
        setLoading(true);
        try {
            const result = await listDataTransferJobs();
            setJobs(result.jobs);
        } catch (e: any) {
            toast({ title: "Failed to load jobs", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        void load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const onDownload = async (job: DataTransferJob) => {
        setDownloadingId(job.id);
        try {
            const { download_url } = await regenerateDataTransferJobDownload(job.id);
            if (typeof window !== "undefined") window.open(download_url, "_blank");
        } catch (e: any) {
            toast({ title: "Download failed", description: e?.message ?? "Unknown error", variant: "destructive" });
        } finally {
            setDownloadingId(null);
        }
    };

    return (
        <div className="space-y-4">
            <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={load} disabled={loading}>
                    {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
                    Refresh
                </Button>
            </div>

            <Table>
                <TableHeader>
                    <TableRow>
                        <TableHead>Status</TableHead>
                        <TableHead>Created</TableHead>
                        <TableHead>Type</TableHead>
                        <TableHead>Records</TableHead>
                        <TableHead>Format</TableHead>
                        <TableHead>Reason</TableHead>
                        <TableHead>Expires</TableHead>
                        <TableHead />
                    </TableRow>
                </TableHeader>
                <TableBody>
                    {jobs.length === 0 && !loading && (
                        <TableRow>
                            <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                                No export batches yet.
                            </TableCell>
                        </TableRow>
                    )}
                    {jobs.map((job) => {
                        const expired = isExpired(job);
                        return (
                            <TableRow key={job.id}>
                                <TableCell>
                                    <div className="flex items-center gap-2">
                                        <StatusIcon status={job.status} />
                                        <span className="capitalize">{job.status}</span>
                                    </div>
                                    {job.status === "failed" && job.error_message && (
                                        <div className="text-xs text-destructive mt-1">{job.error_message}</div>
                                    )}
                                </TableCell>
                                <TableCell>{new Date(job.created_at).toLocaleString()}</TableCell>
                                <TableCell className="capitalize">{job.entity_type}</TableCell>
                                <TableCell>{job.entity_count}</TableCell>
                                <TableCell className="uppercase">{job.format}</TableCell>
                                <TableCell className="max-w-[240px] truncate text-sm text-muted-foreground" title={job.reason ?? undefined}>
                                    {job.reason || "—"}
                                </TableCell>
                                <TableCell>
                                    {job.expires_at ? (
                                        <span className={expired ? "text-destructive" : ""}>
                                            {expired ? "Expired" : new Date(job.expires_at).toLocaleString()}
                                        </span>
                                    ) : (
                                        "—"
                                    )}
                                </TableCell>
                                <TableCell>
                                    {job.status === "completed" && !expired && (
                                        <Button
                                            variant="ghost"
                                            size="sm"
                                            onClick={() => onDownload(job)}
                                            disabled={downloadingId === job.id}
                                        >
                                            {downloadingId === job.id ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : (
                                                <Download className="h-4 w-4" />
                                            )}
                                        </Button>
                                    )}
                                </TableCell>
                            </TableRow>
                        );
                    })}
                </TableBody>
            </Table>
        </div>
    );
}
