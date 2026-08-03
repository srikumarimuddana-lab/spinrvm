// Super-admin Sentry Issues viewer. A thin client over the backend proxy
// (backend/routes/admin/sentry.py), which itself proxies the Sentry Web API.
// Nothing is cached on either side: every call is a live Sentry read, so the
// page's "Refresh" is just a re-fetch with no stale state to clear.

import { request } from "./client";

export type SentrySurface = "backend" | "rider-app" | "driver-app" | "admin" | "unknown";

/** Sentry issue status filter used by the list endpoint / status action. */
export type SentryIssueStatus = "resolved" | "ignored" | "unresolved";

export interface SentryIssue {
    id: string | null;
    short_id: string | null;
    title: string | null;
    culprit: string | null;
    /** Event severity: fatal | error | warning | info | debug. */
    level: string | null;
    /** Sentry's newer triage priority: high | medium | low (may be null). */
    priority: string | null;
    status: string | null;
    substatus: string | null;
    count: number;
    user_count: number | null;
    first_seen: string | null;
    last_seen: string | null;
    permalink: string | null;
    /** Exception type from issue metadata (e.g. "ValueError"). */
    type: string | null;
    /** Exception value/message from issue metadata. */
    value: string | null;
    surface: SentrySurface;
    project: string;
}

export interface SentrySurfaceConfig {
    surface: SentrySurface;
    project: string | null;
    enabled: boolean;
}

export interface SentryConfig {
    configured: boolean;
    org: string | null;
    base_url: string;
    surfaces: SentrySurfaceConfig[];
}

export interface SentryIssuesResponse {
    issues: SentryIssue[];
    count: number;
    surfaces: string[];
    status: string;
    stats_period: string;
    per_project_limit: number;
    /** True when at least one project returned a full page — more may exist. */
    truncated: boolean;
}

export interface SentryStackFrame {
    filename: string | null;
    function: string | null;
    module: string | null;
    lineno: number | null;
    colno: number | null;
    in_app: boolean | null;
    context_line: string | null;
}

export interface SentryException {
    type: string | null;
    value: string | null;
    module: string | null;
    /** Frames ordered crash-site first. */
    frames: SentryStackFrame[];
}

export interface SentryTag {
    key: string;
    value: string;
}

export interface SentryIssueDetail extends SentryIssue {
    exceptions: SentryException[];
    event_id: string | null;
    event_timestamp: string | null;
    tags: SentryTag[];
}

export interface SentryStatusUpdateResponse {
    id: string;
    status: string;
    status_details: Record<string, unknown>;
}

export const getSentryConfig = () => request<SentryConfig>("/api/admin/sentry/config");

export const getSentryIssues = (params?: {
    surface?: string;
    status?: string;
    query?: string;
    statsPeriod?: string;
    limit?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.surface) sp.set("surface", params.surface);
    if (params?.status) sp.set("status", params.status);
    if (params?.query) sp.set("query", params.query);
    if (params?.statsPeriod) sp.set("stats_period", params.statsPeriod);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return request<SentryIssuesResponse>(`/api/admin/sentry/issues${qs ? `?${qs}` : ""}`);
};

export const getSentryIssueDetail = (issueId: string) =>
    request<SentryIssueDetail>(`/api/admin/sentry/issues/${encodeURIComponent(issueId)}`);

/** Change an issue's status. Default "resolved" is the dashboard's Close action. */
export const updateSentryIssueStatus = (issueId: string, status: SentryIssueStatus = "resolved") =>
    request<SentryStatusUpdateResponse>(
        `/api/admin/sentry/issues/${encodeURIComponent(issueId)}/status`,
        {
            method: "POST",
            body: JSON.stringify({ status }),
        },
    );
