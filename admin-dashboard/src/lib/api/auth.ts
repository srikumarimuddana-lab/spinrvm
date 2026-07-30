// Admin auth: login, MFA challenge/enroll/confirm/disable, OTP, session
// logout-all. Extracted from the monolithic lib/api.ts as part of the
// per-domain split.

import { request } from "./client";

/* ── Auth ─────────────────────────────────── */
export interface AuthResponse {
    token: string;
    user: {
        id: string;
        phone: string;
        first_name?: string;
        last_name?: string;
        email?: string;
        role: string;
        profile_complete: boolean;
    };
    is_new_user: boolean;
}

export interface AdminLoginResponse {
    token: string;
    access_expires_at: string;
    csrf_token?: string;
    user: {
        id: string;
        email: string;
        role: string;
        first_name?: string;
        last_name?: string;
        modules?: string[];
    };
}

export interface AdminMfaRequired {
    mfa_required: true;
    mfa_token: string;
}

// ADMIN_MFA_ENFORCED: password was correct but the account has no MFA yet.
// mfa_token is enrollment-scoped — only /mfa/enroll and /mfa/confirm accept it.
export interface AdminMfaEnrollmentRequired {
    mfa_enrollment_required: true;
    mfa_token: string;
}

export type AdminLoginResult = AdminLoginResponse | AdminMfaRequired | AdminMfaEnrollmentRequired;

export const loginAdmin = (phone: string, code: string) =>
    request<AuthResponse>("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
    });

export const loginAdminSession = (email: string, password: string) =>
    request<AdminLoginResult>("/api/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
    });

export const mfaChallenge = (mfa_token: string, totp_code: string) =>
    request<AdminLoginResponse>("/api/admin/auth/mfa/challenge", {
        method: "POST",
        body: JSON.stringify({ mfa_token, totp_code }),
    });

export const mfaStatus = () =>
    request<{ mfa_enabled: boolean; available: boolean; enforced?: boolean }>("/api/admin/auth/mfa/status");

// Confirm also returns full session tokens so first-login enrollment
// (no session yet, only the enrollment-scoped token) lands in the dashboard.
// Settings-flow callers already have a session and can ignore them.
export interface MfaConfirmResponse extends AdminLoginResponse {
    backup_codes: string[];
    refresh_expires_at?: string;
}

// `authToken` carries the enrollment-scoped token during forced first-login
// enrollment; omitted, the session token from the store is used (Settings flow).
// The store token is null in the forced flow, so this header is not overwritten.
export const mfaEnroll = (authToken?: string) =>
    request<{ secret: string; otpauth_uri: string }>("/api/admin/auth/mfa/enroll", {
        method: "POST",
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
    });

export const mfaConfirm = (totp_code: string, authToken?: string) =>
    request<MfaConfirmResponse>("/api/admin/auth/mfa/confirm", {
        method: "POST",
        body: JSON.stringify({ totp_code }),
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
    });

export const mfaDisable = (totp_code: string, password: string) =>
    request<{ success: boolean }>("/api/admin/auth/mfa/disable", {
        method: "POST",
        body: JSON.stringify({ totp_code, password }),
    });

export const sendOtp = (phone: string) =>
    request<{ success: boolean }>("/api/auth/send-otp", {
        method: "POST",
        body: JSON.stringify({ phone }),
    });

// Admin "sign out everywhere" — closes B-P1-13. Bumps
// admin_staff.token_version (kills all in-flight admin access tokens
// on next request) and revokes every refresh token for this staff row.
// Refused server-side for admin-001 (env-var super admin); rotate
// ADMIN_PASSWORD to globally kill that account.
export const logoutAllAdmin = () =>
    request<{ success: boolean; revoked_refresh_tokens: number }>("/api/admin/auth/logout-all", {
        method: "POST",
    });

