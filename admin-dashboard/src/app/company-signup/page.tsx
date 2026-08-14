"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Building2, CheckCircle2, Loader2 } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCompanyAuthStore } from "@/store/companyAuthStore";
import {
    sendCompanyEmailOtp,
    verifyCompanyEmailOtp,
    registerCompany,
} from "@/lib/companyApi";

/**
 * Self-serve company signup (Spinr for Business, M1.5).
 *
 * Three steps, authenticated-first (mirrors /company-login):
 *   1. email  — work email + OTP send
 *   2. code   — verify OTP → portal session (the verified email becomes the
 *               company's contact + the creator becomes the OWNER member;
 *               identity is never taken from the form)
 *   3. form   — company details → POST /api/portal/companies/signup
 *   4. done   — pending-verification confirmation (staff KYB review next)
 */

// Business Terms of Service version recorded with the consent (PIPEDA-style
// versioned consent, matching the users-table pattern). Placeholder until
// legal finalizes the business terms doc — bump on every material change.
const BUSINESS_TERMS_VERSION = "biz-tos-2026-01-draft";

const PROVINCES = [
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT",
];

type Step = "email" | "code" | "form" | "done";

export default function CompanySignupPage() {
    const router = useRouter();
    const completeLogin = useCompanyAuthStore((s) => s.completeLogin);

    const [step, setStep] = useState<Step>("email");
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [email, setEmail] = useState("");
    const [code, setCode] = useState("");

    const [form, setForm] = useState({
        name: "",
        legal_name: "",
        business_number: "",
        industry: "",
        contact_name: "",
        contact_phone: "",
        address_line1: "",
        address_line2: "",
        city: "",
        province: "SK",
        postal_code: "",
        terms_accepted: false,
    });
    const set = (k: keyof typeof form) => (v: string | boolean) =>
        setForm((f) => ({ ...f, [k]: v }));

    const [companyName, setCompanyName] = useState("");

    const normalizedEmail = () => email.trim().toLowerCase();

    const handleSend = async () => {
        setBusy(true);
        setError(null);
        try {
            await sendCompanyEmailOtp(normalizedEmail());
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
            const result = await verifyCompanyEmailOtp(normalizedEmail(), code.trim());
            await completeLogin({
                token: result.token,
                csrfToken: result.csrf_token ?? null,
                user: result.user,
            });
            setStep("form");
        } catch (e) {
            setError(e instanceof Error ? e.message : "Invalid code");
        } finally {
            setBusy(false);
        }
    };

    const formValid =
        form.name.trim() &&
        form.legal_name.trim() &&
        form.business_number.trim() &&
        form.contact_name.trim() &&
        form.address_line1.trim() &&
        form.city.trim() &&
        form.postal_code.trim() &&
        form.terms_accepted;

    const handleSubmit = async () => {
        setBusy(true);
        setError(null);
        try {
            const result = await registerCompany({
                name: form.name.trim(),
                legal_name: form.legal_name.trim(),
                business_number: form.business_number.trim(),
                industry: form.industry.trim() || null,
                contact_name: form.contact_name.trim(),
                contact_phone: form.contact_phone.trim() || null,
                address_line1: form.address_line1.trim(),
                address_line2: form.address_line2.trim() || null,
                city: form.city.trim(),
                province: form.province,
                postal_code: form.postal_code.trim(),
                terms_accepted: form.terms_accepted,
                terms_version: BUSINESS_TERMS_VERSION,
            });
            setCompanyName(result.company.name);
            setStep("done");
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not register the company");
        } finally {
            setBusy(false);
        }
    };

    const field = (
        label: string,
        key: keyof typeof form,
        opts: { placeholder?: string; required?: boolean; hint?: string } = {}
    ) => (
        <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">
                {label}
                {opts.required && <span className="text-red-500"> *</span>}
            </label>
            <Input
                value={form[key] as string}
                placeholder={opts.placeholder}
                onChange={(e) => set(key)(e.target.value)}
            />
            {opts.hint && <p className="text-[11px] text-muted-foreground">{opts.hint}</p>}
        </div>
    );

    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-lg">
                <CardHeader className="space-y-2 text-center">
                    <div className="mx-auto rounded-lg bg-emerald-50 dark:bg-emerald-900/20 p-3 w-fit">
                        <Building2 className="h-7 w-7 text-emerald-600 dark:text-emerald-400" />
                    </div>
                    <CardTitle className="text-xl">Register your company</CardTitle>
                    <p className="text-sm text-muted-foreground">
                        {step === "email" && "Start with your work email — we'll verify it first."}
                        {step === "code" && `Enter the code we emailed to ${normalizedEmail()}.`}
                        {step === "form" && "Tell us about your company. Our team reviews every application."}
                        {step === "done" && "Application received."}
                    </p>
                </CardHeader>
                <CardContent className="space-y-4">
                    {step === "email" && (
                        <>
                            <Input
                                type="email"
                                inputMode="email"
                                placeholder="name@company.com"
                                value={email}
                                autoComplete="email"
                                onChange={(e) => setEmail(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && !busy && normalizedEmail() && handleSend()}
                            />
                            <Button className="w-full" onClick={handleSend} disabled={busy || !normalizedEmail()}>
                                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Send verification code
                            </Button>
                            <button
                                type="button"
                                className="w-full text-center text-xs text-muted-foreground hover:underline"
                                onClick={() => router.push("/company-login")}
                            >
                                Already registered? Sign in
                            </button>
                        </>
                    )}

                    {step === "code" && (
                        <>
                            <Input
                                type="text"
                                inputMode="numeric"
                                placeholder="4-digit code"
                                maxLength={6}
                                value={code}
                                onChange={(e) => setCode(e.target.value)}
                                onKeyDown={(e) => e.key === "Enter" && !busy && code && handleVerify()}
                            />
                            <Button className="w-full" onClick={handleVerify} disabled={busy || !code.trim()}>
                                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Verify email
                            </Button>
                            <button
                                type="button"
                                className="w-full text-center text-xs text-muted-foreground hover:underline"
                                onClick={() => {
                                    setStep("email");
                                    setCode("");
                                    setError(null);
                                }}
                            >
                                Use a different email
                            </button>
                        </>
                    )}

                    {step === "form" && (
                        <div className="space-y-3">
                            {field("Company name", "name", { placeholder: "Acme Corp", required: true })}
                            {field("Legal name", "legal_name", {
                                placeholder: "Acme Corporation Inc.",
                                required: true,
                            })}
                            {field("CRA business number", "business_number", {
                                placeholder: "123456789RT0001",
                                required: true,
                                hint: "9 digits, optionally followed by a CRA program account (e.g. RT0001).",
                            })}
                            {field("Industry", "industry", { placeholder: "Automotive" })}
                            <div className="grid grid-cols-2 gap-3">
                                {field("Contact name", "contact_name", { placeholder: "Jane Doe", required: true })}
                                {field("Contact phone", "contact_phone", { placeholder: "+1 306 555 0100" })}
                            </div>
                            {field("Address line 1", "address_line1", { placeholder: "123 Main St", required: true })}
                            {field("Address line 2", "address_line2", { placeholder: "Suite 400" })}
                            <div className="grid grid-cols-3 gap-3">
                                {field("City", "city", { placeholder: "Saskatoon", required: true })}
                                <div className="space-y-1">
                                    <label
                                        htmlFor="signup-province"
                                        className="text-xs font-medium text-muted-foreground"
                                    >
                                        Province<span className="text-red-500"> *</span>
                                    </label>
                                    <select
                                        id="signup-province"
                                        className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                                        value={form.province}
                                        onChange={(e) => set("province")(e.target.value)}
                                    >
                                        {PROVINCES.map((p) => (
                                            <option key={p} value={p}>
                                                {p}
                                            </option>
                                        ))}
                                    </select>
                                </div>
                                {field("Postal code", "postal_code", { placeholder: "S7K 1M1", required: true })}
                            </div>
                            <label className="flex items-start gap-2 text-xs text-muted-foreground">
                                <input
                                    type="checkbox"
                                    className="mt-0.5"
                                    checked={form.terms_accepted}
                                    onChange={(e) => set("terms_accepted")(e.target.checked)}
                                />
                                <span>
                                    I am authorized to register this company and accept the Spinr for
                                    Business terms of service on its behalf.
                                </span>
                            </label>
                            <Button className="w-full" onClick={handleSubmit} disabled={busy || !formValid}>
                                {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                                Submit application
                            </Button>
                        </div>
                    )}

                    {step === "done" && (
                        <div className="space-y-4 text-center">
                            <CheckCircle2 className="mx-auto h-10 w-10 text-emerald-600" />
                            <p className="text-sm">
                                <span className="font-medium">{companyName}</span> is registered and awaiting
                                verification. We review every application and will email you at{" "}
                                <span className="font-medium">{normalizedEmail()}</span> once your account is
                                approved.
                            </p>
                            <Button className="w-full" onClick={() => router.replace("/company-portal")}>
                                Go to the business portal
                            </Button>
                        </div>
                    )}

                    {error && <p className="rounded bg-destructive/10 p-3 text-sm text-destructive">{error}</p>}
                </CardContent>
            </Card>
        </div>
    );
}
