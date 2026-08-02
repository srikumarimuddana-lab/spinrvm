// Corporate accounts, wallet, member allowances, policy, allowed domains,
// and billing. Extracted from the monolithic lib/api.ts as part of the
// per-domain split — this is the largest single money-moving domain (wallet
// deltas go through corporate_wallet_apply_delta on the backend).

import { request } from "./client";
import { useAuthStore } from "@/store/authStore";

/* ── Corporate Accounts ─────────────────────── */
export type CompanyStatus =
    | "pending_verification"
    | "active"
    | "suspended"
    | "closed";

export type SizeTier = "smb" | "mid_market" | "enterprise";

export interface CorporateAccount {
    id: string;
    name: string;
    legal_name?: string | null;
    business_number?: string | null;
    tax_region?: string | null;
    billing_email?: string | null;
    contact_name?: string | null;
    contact_email?: string | null;
    contact_phone?: string | null;
    status: CompanyStatus;
    size_tier: SizeTier;
    kyb_document_url?: string | null;
    kyb_reviewed_at?: string | null;
    kyb_reviewed_by?: string | null;
    kyb_submitted_at?: string | null;
    kyb_review_note?: string | null;
    kyb_last_decision?: "approved" | "rejected" | null;
    credit_limit?: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
}

export const getCorporateAccounts = () =>
    request<CorporateAccount[]>("/api/admin/corporate-accounts");

export const listCorporateAccounts = (opts: {
    status?: CompanyStatus;
    size_tier?: SizeTier;
    search?: string;
    skip?: number;
    limit?: number;
} = {}) => {
    const p = new URLSearchParams();
    if (opts.status) p.set("status", opts.status);
    if (opts.size_tier) p.set("size_tier", opts.size_tier);
    if (opts.search) p.set("search", opts.search);
    if (opts.skip != null) p.set("skip", String(opts.skip));
    if (opts.limit != null) p.set("limit", String(opts.limit));
    const qs = p.toString();
    return request<CorporateAccount[]>(
        `/api/admin/corporate-accounts${qs ? `?${qs}` : ""}`
    );
};

export const reviewKyb = (id: string, decision: { approve: boolean; note?: string }) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/kyb-review`, {
        method: "POST",
        body: JSON.stringify(decision),
    });

export const getCorporateAccount = (id: string) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`);

export const changeCompanyStatus = (
    id: string,
    transition: { status: CompanyStatus; reason?: string }
) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/status`, {
        method: "POST",
        body: JSON.stringify(transition),
    });

// Blob-fetch the KYB document through the backend streaming endpoint
// (kyb_document_url is a raw PRIVATE-bucket key, not a browser-usable URL).
export async function fetchKybDocumentBlob(id: string): Promise<Blob> {
    const token = useAuthStore.getState().token;
    const res = await fetch(`/api/admin/corporate-accounts/${id}/kyb/view`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`Could not load document (${res.status})`);
    return res.blob();
}

export const createCorporateAccount = (data: any) =>
    request<CorporateAccount>("/api/admin/corporate-accounts", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateCorporateAccount = (id: string, data: any) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteCorporateAccount = (id: string) =>
    request<any>(`/api/admin/corporate-accounts/${id}`, { method: "DELETE" });

/* ── Corporate Wallet ─────────────────────── */
export interface WalletTxn {
    id: string;
    type: string;
    scope: string;
    amount: string;
    balance_after: string;
    created_at: string;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
}

export interface CorporateWallet {
    id: string;
    company_id: string;
    balance: string;
    currency: string;
    auto_topup_enabled: boolean;
    auto_topup_threshold: string | null;
    auto_topup_amount: string | null;
    auto_topup_daily_cap: string;
    soft_negative_floor: string;
    transactions: WalletTxn[];
}

export type WalletConfigPatch = Partial<
    Pick<
        CorporateWallet,
        | "auto_topup_enabled"
        | "auto_topup_threshold"
        | "auto_topup_amount"
        | "auto_topup_daily_cap"
    >
>;

export const getCorporateWallet = (companyId: string) =>
    request<CorporateWallet>(`/api/admin/corporate-accounts/${companyId}/wallet`);

export const updateWalletConfig = (companyId: string, patch: WalletConfigPatch) =>
    request<CorporateWallet>(
        `/api/admin/corporate-accounts/${companyId}/wallet/config`,
        { method: "PUT", body: JSON.stringify(patch) }
    );

export const walletTopupIntent = (
    companyId: string,
    body: { amount: number; payment_method_id?: string }
) =>
    request<{ payment_intent_id: string; client_secret: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/topup`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const walletAdjust = (
    companyId: string,
    body: { amount: number; notes: string }
) =>
    request<{ transaction_id: string; balance_after: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/adjust`,
        { method: "POST", body: JSON.stringify(body) }
    );

// Corporate + admin portal review, round 2: "no portfolio-level view of
// corporate wallet risk."
export type WalletRiskFlag = "negative_balance" | "at_floor" | "below_autotopup_threshold" | "low_balance_no_autotopup";

export interface WalletRiskEntry {
    wallet_id: string;
    company_id: string;
    company_name: string | null;
    company_status: string | null;
    balance: string;
    soft_negative_floor: string;
    auto_topup_enabled: boolean;
    risk_flags: WalletRiskFlag[];
}

export interface WalletRiskPortfolio {
    total_wallets: number;
    flagged_count: number;
    wallets: WalletRiskEntry[];
}

export const getWalletRiskPortfolio = () =>
    request<WalletRiskPortfolio>("/api/admin/corporate-accounts/wallet-portfolio");

/* ── Corporate KYB re-verification staleness (round 2) — visibility
   only, never auto-changes a company's status. ── */
export interface KybReverificationCompany {
    id: string;
    name: string | null;
    legal_name: string | null;
    kyb_reviewed_at: string | null;
    kyb_reviewed_by: string | null;
}

export interface KybReverificationDue {
    threshold_months: number;
    count: number;
    companies: KybReverificationCompany[];
}

export const getKybReverificationDue = () =>
    request<KybReverificationDue>("/api/admin/corporate-accounts/kyb-reverification-due");

/* ── Corporate subscription billing (flat SaaS, round 2) ── */
export type CorporateSubscriptionStatus = "active" | "past_due" | "cancelled";

export interface CorporateSubscriptionPlan {
    id: string;
    name: string;
    monthly_price: string;
    description?: string | null;
    is_active: boolean;
}

export interface CorporateSubscription {
    id: string;
    company_id: string;
    plan_id: string | null;
    plan_name: string;
    price: string;
    status: CorporateSubscriptionStatus;
    current_period_end: string | null;
    cancel_at_period_end: boolean;
    started_at: string;
    cancelled_at: string | null;
    created_at: string;
}

export interface CompanySubscriptionResponse {
    current: CorporateSubscription | null;
    history: CorporateSubscription[];
}

export const getSubscriptionPlans = () =>
    request<{ plans: CorporateSubscriptionPlan[] }>("/api/admin/corporate-accounts/subscription-plans");

export const getCompanySubscription = (companyId: string) =>
    request<CompanySubscriptionResponse>(`/api/admin/corporate-accounts/${companyId}/subscription`);

export const assignCompanySubscription = (companyId: string, planId: string) =>
    request<CorporateSubscription>(`/api/admin/corporate-accounts/${companyId}/subscription`, {
        method: "POST",
        body: JSON.stringify({ plan_id: planId }),
    });

export const cancelCompanySubscription = (companyId: string, atPeriodEnd: boolean = true) =>
    request<CorporateSubscription>(`/api/admin/corporate-accounts/${companyId}/subscription/cancel`, {
        method: "POST",
        body: JSON.stringify({ at_period_end: atPeriodEnd }),
    });

/* ── Corporate members / allowances (Plan 3) ── */
export type CorporateMemberRole = "owner" | "admin" | "member";
export type CorporateMemberStatus = "invited" | "active" | "suspended" | "removed";
export type AllowanceTypeValue = "fixed_recurring" | "one_time" | "unlimited";

export interface CorporateMember {
    id: string;
    company_id: string;
    user_id?: string | null;
    role: CorporateMemberRole;
    status: CorporateMemberStatus;
    invited_email?: string | null;
    created_at?: string;
    updated_at?: string;
}

export interface CorporateAllowance {
    id: string;
    member_id: string;
    type: AllowanceTypeValue;
    amount?: number | null;
    used: number;
    period_start?: string | null;
    period_end?: string | null;
    rollover?: boolean;
    auto_approve_topup_amount?: number | null;
    auto_approve_monthly_count?: number | null;
    status: "active" | "paused" | "expired";
}

export interface AllowanceRequestRow {
    id: string;
    member_id: string;
    amount: number;
    reason: string;
    status: "pending" | "approved" | "denied" | "auto_approved";
    reviewed_by?: string | null;
    decision_notes?: string | null;
    created_at?: string;
}

export const listCompanyMembers = (companyId: string, status?: string) =>
    request<CorporateMember[]>(
        `/api/company/${companyId}/members${status ? `?status=${encodeURIComponent(status)}` : ""}`
    );

export const inviteCompanyMember = (
    companyId: string,
    body: { email: string; role: CorporateMemberRole; policy_override?: boolean }
) =>
    request<{ member: CorporateMember; invite_url: string }>(
        `/api/company/${companyId}/members/invite`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const removeCompanyMember = (companyId: string, memberId: string) =>
    request<CorporateMember>(`/api/company/${companyId}/members/${memberId}`, {
        method: "DELETE",
    });

export const getMemberAllowance = (companyId: string, memberId: string) =>
    request<CorporateAllowance | Record<string, never>>(
        `/api/company/${companyId}/members/${memberId}/allowance`
    );

export const putMemberAllowance = (
    companyId: string,
    memberId: string,
    body: {
        type: AllowanceTypeValue;
        amount?: number | null;
        period_start?: string | null;
        period_end?: string | null;
        rollover?: boolean;
        auto_approve_topup_amount?: number | null;
        auto_approve_monthly_count?: number | null;
    }
) =>
    request<CorporateAllowance>(
        `/api/company/${companyId}/members/${memberId}/allowance`,
        { method: "PUT", body: JSON.stringify(body) }
    );

export const listCompanyAllowanceRequests = (companyId: string, status = "pending") =>
    request<AllowanceRequestRow[]>(
        `/api/company/${companyId}/allowance-requests?status=${encodeURIComponent(status)}`
    );

export const decideAllowanceRequest = (
    companyId: string,
    requestId: string,
    body: { approve: boolean; note?: string }
) =>
    request<AllowanceRequestRow>(
        `/api/company/${companyId}/allowance-requests/${requestId}/decide`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const updateCompanyMember = (
    companyId: string,
    memberId: string,
    body: { role?: CorporateMemberRole; status?: CorporateMemberStatus; policy_override?: boolean }
) =>
    request<CorporateMember>(
        `/api/company/${companyId}/members/${memberId}`,
        { method: "PATCH", body: JSON.stringify(body) }
    );

/* ── Company policy (Plan 6) ── */
export type PaymentSourcePolicy = "allowance_only" | "master_only" | "both";

export interface TimeWindowPolicy {
    day: "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
    start: string;
    end: string;
}

export interface CorporatePolicy {
    id?: string;
    company_id?: string;
    active: boolean;
    max_fare_per_ride?: number | null;
    allowed_geofence?: Record<string, unknown> | null;
    allowed_time_windows?: TimeWindowPolicy[] | null;
    allowed_payment_source: PaymentSourcePolicy;
}

export const getCompanyPolicy = (companyId: string) =>
    request<CorporatePolicy | Record<string, never>>(`/api/company/${companyId}/policy`);

export const putCompanyPolicy = (
    companyId: string,
    body: Omit<CorporatePolicy, "id" | "company_id">
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PUT",
        body: JSON.stringify(body),
    });

export const patchCompanyPolicy = (
    companyId: string,
    body: Partial<Omit<CorporatePolicy, "id" | "company_id">>
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

/* ── Company allowed domains (Plan 7) ── */
export interface AllowedDomainRow {
    company_id: string;
    domain: string;
}

export const listAllowedDomains = (companyId: string) =>
    request<AllowedDomainRow[]>(`/api/company/${companyId}/allowed-domains`);

export const addAllowedDomain = (companyId: string, domain: string) =>
    request<AllowedDomainRow>(`/api/company/${companyId}/allowed-domains`, {
        method: "POST",
        body: JSON.stringify({ domain }),
    });

export const removeAllowedDomain = (companyId: string, domain: string) =>
    request<{ status: string }>(
        `/api/company/${companyId}/allowed-domains/${encodeURIComponent(domain)}`,
        { method: "DELETE" }
    );

/* ── Company billing (Plan 6) ── */
export interface BillingMemberBreakdown {
    member_id: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
}

// Corporate + admin portal review, round 2: "no GST/PST breakdown on
// corporate statements" — tax_total / tax_by_type surface what was
// already computed and stored per-ride, for input-tax-credit reconciliation.
export type TaxByType = Record<string, number>;

export interface BillingSummary {
    month: string;
    wallet_balance: number;
    wallet_currency: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
    avg_fare: number;
    tax_total: number;
    tax_by_type: TaxByType;
    by_member: BillingMemberBreakdown[];
}

export interface BillingLineItem {
    ride_id: string;
    member_id: string;
    source_type: string;
    allowance_debit_amount: number;
    master_fallback_amount: number;
    policy_check_result?: string;
    created_at: string;
    tax_amount?: number;
    tax_breakdown?: Record<string, { rate: number; amount: number }>;
}

export interface BillingStatement {
    month: string;
    from: string;
    to: string;
    line_items: BillingLineItem[];
    summary: {
        ride_count: number;
        allowance_total: number;
        master_total: number;
        total: number;
        avg_fare: number;
        tax_total: number;
        tax_by_type: TaxByType;
        by_member: BillingMemberBreakdown[];
    };
}

export interface BillingTransaction {
    id: string;
    type: string;
    amount: number;
    balance_after?: number;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
    stripe_payment_intent_id?: string | null;
    created_at: string;
}

export interface BillingTransactionsPage {
    wallet_id: string;
    balance: number;
    currency: string;
    transactions: BillingTransaction[];
}

export const getCompanyBillingSummary = (companyId: string, month?: string) => {
    const qs = month ? `?month=${encodeURIComponent(month)}` : "";
    return request<BillingSummary>(`/api/company/${companyId}/billing/summary${qs}`);
};

export const getCompanyBillingStatement = (companyId: string, month: string) =>
    request<BillingStatement>(
        `/api/company/${companyId}/billing/statements/${encodeURIComponent(month)}`
    );

export const getCompanyBillingTransactions = (
    companyId: string,
    skip = 0,
    limit = 50
) =>
    request<BillingTransactionsPage>(
        `/api/company/${companyId}/billing/transactions?skip=${skip}&limit=${limit}`
    );

