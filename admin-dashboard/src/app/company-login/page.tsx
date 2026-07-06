"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Building2, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCompanyAuthStore } from "@/store/companyAuthStore";
import {
    sendCompanyOtp,
    verifyCompanyOtp,
    getMyWorkProfiles,
    acceptCompanyInvite,
    portalHome,
} from "@/lib/companyApi";

/**
 * Company-portal login (Spinr for Business).
 *
 * Company staff sign in with the SAME phone-OTP identity as the rider app —
 * their corporate membership (invite / email-domain match) is what grants
 * portal access, so there are no separate business passwords to manage.
 *
 * `?invite_token=` deep links accept a member invitation right after OTP
 * verification, closing the "employee never installed the app" gap in the
 * invite flow (the app deep link app://join is useless on a desk computer).
 */
function CompanyLoginInner() {
    const router = useRouter();
    const params = useSearchParams();
    const completeLogin = useCompanyAuthStore((s) => s.completeLogin);
    const setMemberships = useCompanyAuthStore((s) => s.setMemberships);

    const [step, setStep] = useState<"phone" | "code">("phone");
    const [phone, setPhone] = useState("");
    const [code, setCode] = useState("");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const normalizedPhone = () => {
        const digits = phone.replace(/\D/g, "");
        if (digits.length === 10) return `+1${digits}`;
        // Common 11-digit forms like "+1 (306) 555-0123" / "1-306-555-0123":
        // the backend send-otp only accepts strict +1XXXXXXXXXX.
        if (digits.length === 11 && digits.startsWith("1")) return `+${digits}`;
        return phone.trim();
    };

    const handleSend = async () => {
        setBusy(true);
        setError(null);
        try {
            await sendCompanyOtp(normalizedPhone());
            setStep("code");
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not send code");
        } finally {
            setBusy(false);
        }
    };

    const handleVerify = async () => {
        setBusy(true);
        setError(null);
        try {
            const result = await verifyCompanyOtp(normalizedPhone(), code.trim());
            await completeLogin({
                token: result.token,
                csrfToken: result.csrf_token ?? null,
                user: result.user,
            });

            // Accept both param names: the portal surfaces web links as
            // ?invite_token=, but the backend also mints app://join?token= deep
            // links — honour `token` too so either lands the membership.
            const inviteToken = params.get("invite_token") || params.get("token");
            if (inviteToken) {
                try {
                    await acceptCompanyInvite(inviteToken);
                } catch (e) {
                    // Already-accepted / expired invites shouldn't block login —
                    // the membership list below is the source of truth.
                    console.warn("[company-login] invite accept failed:", e);
                }
            }

            const memberships = await getMyWorkProfiles().catch(() => []);
            setMemberships(memberships);

            if (memberships.length === 0) {
                setError(
                    "This phone number has no company memberships. Ask your company admin for an invite link."
                );
                setBusy(false);
                return;
            }

            const next = params.get("next");
            if (next && next.startsWith("/company-portal")) {
                router.replace(next);
            } else if (memberships.length === 1) {
                // Role-appropriate landing: members can't see the admin overview.
                router.replace(portalHome(memberships[0].company.id, memberships[0].membership.role));
            } else {
                router.replace("/company-portal");
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : "Invalid code");
            setBusy(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-md">
                <CardHeader className="space-y-2 text-center">
                    <div className="mx-auto rounded-lg bg-emerald-50 p-3 w-fit">
                        <Building2 className="h-7 w-7 text-emerald-600" />
                    </div>
                    <CardTitle className="text-xl">Spinr for Business</CardTitle>
                    <p className="text-sm text-muted-foreground">
                        {step === "phone"
                            ? "Sign in with your phone number to manage your company's rides."
                            : `Enter the code we texted to ${phone}.`}
                    </p>
                </CardHeader>
                <CardContent className="space-y-4">
                    {step === "phone" ? (
                        <>
                            <Input
                                type="tel"
                                inputMode="tel"
                                placeholder="(306) 555-0123"
                                value={phone}
                                autoFocus
                                onChange={(e) => setPhone(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && !busy && phone && handleSend()}
                            />
                            <Button className="w-full" onClick={handleSend} disabled={busy || !phone.trim()}>
                                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Send code
                            </Button>
                        </>
                    ) : (
                        <>
                            <Input
                                type="text"
                                inputMode="numeric"
                                placeholder="4-digit code"
                                maxLength={6}
                                value={code}
                                autoFocus
                                onChange={(e) => setCode(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && !busy && code && handleVerify()}
                            />
                            <Button className="w-full" onClick={handleVerify} disabled={busy || !code.trim()}>
                                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Verify &amp; sign in
                            </Button>
                            <button
                                type="button"
                                className="w-full text-center text-xs text-muted-foreground hover:underline"
                                onClick={() => {
                                    setStep("phone");
                                    setCode("");
                                    setError(null);
                                }}
                            >
                                Use a different number
                            </button>
                        </>
                    )}
                    {error && (
                        <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>
                    )}
                    <p className="text-center text-xs text-muted-foreground">
                        Employees use the same sign-in as the Spinr rider app. Your company
                        membership controls what you can see here.
                    </p>
                </CardContent>
            </Card>
        </div>
    );
}

export default function CompanyLoginPage() {
    return (
        <Suspense fallback={null}>
            <CompanyLoginInner />
        </Suspense>
    );
}
