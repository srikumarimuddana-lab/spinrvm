// Rider/driver/admin user records and the admin wallet credit/debit
// actions. Extracted from the monolithic lib/api.ts as part of the
// per-domain split.

import { request } from "./client";

/* ── Users (Riders + Drivers + Admins) ───────── */
// NOTE: no `search` param here on purpose — search terms can be phone
// numbers/emails and GET query strings land in browser history and proxy
// logs. Use the POST-based adminSearchUsers for user search.
export const getUsers = (role: "all" | "rider" | "driver" | "admin" = "all") =>
    request<any[]>(`/api/admin/users?role=${role}`);

export const getUsersPaginated = (opts: {
    role?: "all" | "rider" | "driver" | "both" | "admin";
    search?: string;
    limit?: number;
    offset?: number;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("role", opts.role ?? "all");
    if (opts.search) sp.set("search", opts.search);
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    return request<any[]>(`/api/admin/users?${sp.toString()}`);
};

export const getUserDetails = (id: string) =>
    request<any>(`/api/admin/users/${id}`);

export const updateUserStatus = (id: string, statusData: any) =>
    request<any>(`/api/admin/users/${id}/status`, {
        method: "PUT",
        body: JSON.stringify(statusData),
    });

export const updateUserFlags = (id: string, flags: { is_rider?: boolean; is_driver?: boolean }) =>
    request<any>(`/api/admin/users/${id}/role`, {
        method: "PATCH",
        body: JSON.stringify(flags),
    });

export const exportUsers = (limit = 1000) =>
    request<{ users: any[]; count: number }>(`/api/admin/export/users?limit=${limit}`);

export const logPiiReveal = (entityType: string, entityId: string) =>
    request<{ ok: boolean }>("/api/admin/audit/pii-reveal", {
        method: "POST",
        body: JSON.stringify({ entity_type: entityType, entity_id: entityId }),
    });

/* ── Wallet (admin) ─────────────────────────── */
export const getUserWallet = (userId: string, limit = 50) =>
    request<{
        user: { id: string; name: string; phone: string; email: string };
        wallet: { id: string; balance: number; currency: string; is_active: boolean };
        transactions: Array<{
            id: string;
            type: string;
            amount: number;
            balance_after: number;
            description: string | null;
            reference_id: string | null;
            metadata: Record<string, any>;
            created_at: string;
        }>;
    }>(`/api/admin/wallet/${userId}?limit=${limit}`);

export const creditUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/credit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });

export const debitUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/debit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });


/* ── Stripe customer email backfill (super_admin) ─────────────────────────
 * Attaches riders' emails to Stripe customers created before the
 * email-mapping change, so a rider is findable by address in the Stripe
 * dashboard instead of needing a Supabase round-trip first.
 *
 * apply=false is a pure preview — every customer is retrieved from Stripe and
 * the exact diff returned, but nothing is written. That is what the UI shows
 * before asking to confirm.
 *
 * Responses carry IDs only, never email addresses (PIPEDA). */
export const backfillStripeCustomerEmails = (opts?: {
    user_ids?: string[];
    emails?: string[];
    limit?: number;
    /* Resume token from a previous response's next_cursor. Omitting it restarts
     * at the first page, which is why the caller must loop on next_cursor
     * rather than re-issuing a bare call to "continue". */
    cursor?: string;
    apply?: boolean;
}) =>
    request<{
        applied: boolean;
        scanned: number;
        updated: number;
        unchanged: number;
        no_email: number;
        skipped_deleted: number;
        has_more: boolean;
        next_cursor: string | null;
        /* More pages, throttled rows, or failures — the run did not finish. */
        incomplete: boolean;
        key_mode: string;
        changes: { user_id: string; customer_id: string; had_email: boolean }[];
        missing_on_key: string[];
        /* Rate-limited by Stripe after the service's retries. Outstanding, not
         * broken — a later run picks these up. */
        throttled: string[];
        failed: string[];
        note: string;
    }>(`/api/admin/stripe/customer-emails/backfill`, {
        method: "POST",
        body: JSON.stringify(opts ?? {}),
    });
