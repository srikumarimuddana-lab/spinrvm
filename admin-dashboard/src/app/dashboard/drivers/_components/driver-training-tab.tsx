// Training (LMS) tab of the drivers detail slideout. Pure code motion out
// of drivers/page.tsx (design-audit follow-up,
// docs/change-log/2026-09-04-breakup-drivers-page-god-component.md) — no
// logic changes.

import type { DriverTraining } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RefreshCw, GraduationCap, Award, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useFeatureFlag } from "@/hooks/useFeatureFlag";

/* eslint-disable no-restricted-syntax -- categorical training-status map (5 states), not a
   success/warning/destructive signal — too many states for the 3-token system (#2816) */
export const TRAINING_STATUS_STYLES: Record<string, string> = {
    completed: "bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300",
    in_progress: "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300",
    registered: "bg-violet-100 dark:bg-violet-900/30 text-violet-700 dark:text-violet-300",
    invited: "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300",
    not_invited: "bg-muted text-muted-foreground",
};
/* eslint-enable no-restricted-syntax */

// Quiet Console Stage 3: flag-on Badge variant per training status —
// "invited" reads as a pending/attention state, "completed" as a positive
// one; the rest are workflow stages with no valence, so they share plain
// `outline`. TRAINING_STATUS_STYLES itself is untouched.
export const TRAINING_STATUS_BADGE_VARIANT: Record<string, "outline" | "outline-success" | "outline-warning"> = {
    completed: "outline-success",
    in_progress: "outline",
    registered: "outline",
    invited: "outline-warning",
    not_invited: "outline",
};

export function DriverTrainingTab({ data, loading, error, onRefresh, fmtDate }: {
    data: DriverTraining | null;
    loading: boolean;
    error: string | null;
    onRefresh: () => void;
    fmtDate: (d: string) => string;
}) {
    // Quiet Console Stage 3: gates the flag-on Badge alternate for the
    // training-status pill below — TRAINING_STATUS_STYLES stays the flag-off path.
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
    if (loading) {
        return <div className="text-sm text-muted-foreground py-10 text-center">Loading training data from the LMS…</div>;
    }
    if (error) {
        return (
            <div className="py-10 text-center space-y-3">
                <p className="text-sm text-muted-foreground">{error}</p>
                <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Retry</Button>
            </div>
        );
    }
    if (!data) {
        return <div className="text-sm text-muted-foreground py-10 text-center">No training data.</div>;
    }
    if (!data.matched) {
        return (
            <div className="py-10 text-center space-y-3">
                <GraduationCap className="w-8 h-8 mx-auto text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                    {data.reason === "no_phone"
                        ? "This driver has no phone number on file, so they can't be matched against the LMS."
                        : `No LMS driver record matches this phone number${data.phone_last4 ? ` (ending in ${data.phone_last4})` : ""}.`}
                </p>
                <Button variant="outline" size="sm" onClick={onRefresh}><RefreshCw className="w-3.5 h-3.5 mr-1.5" />Refresh</Button>
            </div>
        );
    }

    const lms = data.lms!;
    const t = lms.training;
    const statusLabel = (t.status || "unknown").replace(/_/g, " ");
    const pct = Math.max(0, Math.min(100, Math.round(t.completion_percentage ?? 0)));
    const quizAttempts = lms.history?.quiz_attempts || [];
    const communications = lms.history?.communications || [];

    return (
        <div className="space-y-5">
            {/* Status summary */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Training Status</p>
                    {themeV2Enabled ? (
                        <Badge variant={TRAINING_STATUS_BADGE_VARIANT[t.status] ?? "outline"} className="text-[10px] font-bold uppercase tracking-wide mt-1.5">{statusLabel}</Badge>
                    ) : (
                        <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide mt-1.5 ${TRAINING_STATUS_STYLES[t.status] || "bg-muted text-muted-foreground"}`}>
                            {statusLabel}
                        </span>
                    )}
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Registered</p>
                    <p className="text-sm font-semibold mt-1">
                        {t.registered ? `Yes${t.registered_at ? ` · ${fmtDate(t.registered_at)}` : ""}` : "No"}
                    </p>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Completion</p>
                    <p className="text-xl font-bold mt-0.5">{pct}%</p>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5">
                        <div className={`h-full rounded-full ${pct >= 100 ? "bg-success" : "bg-primary"}`} style={{ width: `${pct}%` }} />
                    </div>
                </div>
                <div className="rounded-xl border border-border/50 p-3">
                    <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium">Completed</p>
                    <p className="text-sm font-semibold mt-1">{t.completed_at ? fmtDate(t.completed_at) : "—"}</p>
                </div>
            </div>
            <p className="text-xs text-muted-foreground flex items-center gap-2">
                Matched by phone{data.phone_last4 ? ` ending in ${data.phone_last4}` : ""} · LMS record for <span className="font-semibold text-foreground">{lms.driver.full_name}</span>
                <button type="button" onClick={onRefresh} className="inline-flex items-center gap-1 text-primary hover:underline" title="Bypass the cache and re-fetch from the LMS">
                    <RefreshCw className="w-3 h-3" />Refresh
                </button>
            </p>

            {/* Certificates */}
            <div>
                <h3 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><Award className="w-4 h-4 text-muted-foreground" />Certificates</h3>
                {lms.certificates.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center">No certificates issued yet.</p>
                ) : (
                    <div className="space-y-2">
                        {lms.certificates.map((c, i) => (
                            <div key={i} className="rounded-xl border border-border/50 p-3 flex items-center gap-3">
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-mono font-semibold truncate">{c.certificate_number}</p>
                                    <p className="text-xs text-muted-foreground mt-0.5">
                                        {c.course_title}
                                        {c.final_quiz_score != null ? ` · final score ${Math.round(Number(c.final_quiz_score))}%` : ""}
                                        {` · issued ${fmtDate(c.issued_at)}`}
                                        {c.expires_at ? ` · expires ${fmtDate(c.expires_at)}` : ""}
                                    </p>
                                </div>
                                <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wide shrink-0 ${c.status === "active" ? "bg-success/15 text-success" : "bg-destructive/15 text-destructive"}`}>
                                    {c.status}
                                </span>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Courses */}
            {t.courses.length > 0 && (
                <div>
                    <h3 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><GraduationCap className="w-4 h-4 text-muted-foreground" />Courses</h3>
                    <div className="space-y-2">
                        {t.courses.map((c, i) => {
                            const coursePct = Math.max(0, Math.min(100, Math.round(c.progress ?? 0)));
                            return (
                                <div key={i} className="rounded-xl border border-border/50 p-3">
                                    <div className="flex items-center justify-between gap-2">
                                        <p className="text-sm font-medium truncate">{c.course_title || "Course"}</p>
                                        <span className="text-[11px] text-muted-foreground shrink-0">{coursePct}% · {(c.status || "").replace(/_/g, " ")}</span>
                                    </div>
                                    <div className="h-1.5 rounded-full bg-muted overflow-hidden mt-1.5">
                                        <div className={`h-full rounded-full ${coursePct >= 100 ? "bg-success" : "bg-primary"}`} style={{ width: `${coursePct}%` }} />
                                    </div>
                                    <p className="text-[11px] text-muted-foreground mt-1">
                                        Enrolled {c.enrolled_at ? fmtDate(c.enrolled_at) : "—"}
                                        {c.completed_at ? ` · completed ${fmtDate(c.completed_at)}` : ""}
                                    </p>
                                </div>
                            );
                        })}
                    </div>
                </div>
            )}

            {/* History */}
            <div>
                <h3 className="text-sm font-semibold flex items-center gap-1.5 mb-2"><Clock className="w-4 h-4 text-muted-foreground" />History</h3>
                {quizAttempts.length === 0 && communications.length === 0 ? (
                    <p className="text-sm text-muted-foreground py-4 text-center">No training history yet.</p>
                ) : (
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium mb-1.5">Quiz Attempts</p>
                            {quizAttempts.length === 0 ? (
                                <p className="text-xs text-muted-foreground">None.</p>
                            ) : (
                                <div className="space-y-1.5">
                                    {quizAttempts.map((q, i) => (
                                        <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 px-2.5 py-1.5">
                                            <span className="text-xs truncate">{q.quiz_title || "Quiz"} · {Math.round(Number(q.score))}%</span>
                                            <span className="flex items-center gap-1.5 shrink-0">
                                                <span className={`text-[10px] font-bold uppercase ${q.passed ? "text-success" : "text-destructive"}`}>{q.passed ? "Pass" : "Fail"}</span>
                                                <span className="text-[10px] text-muted-foreground">{fmtDate(q.attempted_at)}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                        <div>
                            <p className="text-[11px] uppercase tracking-wide text-muted-foreground font-medium mb-1.5">Communications</p>
                            {communications.length === 0 ? (
                                <p className="text-xs text-muted-foreground">None.</p>
                            ) : (
                                <div className="space-y-1.5">
                                    {communications.map((m, i) => (
                                        <div key={i} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 px-2.5 py-1.5">
                                            <span className="text-xs truncate">{(m.message_type || "message").replace(/_/g, " ")} ({m.communication_type})</span>
                                            <span className="flex items-center gap-1.5 shrink-0">
                                                <span className="text-[10px] text-muted-foreground uppercase">{m.status}</span>
                                                <span className="text-[10px] text-muted-foreground">{fmtDate(m.sent_at)}</span>
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
