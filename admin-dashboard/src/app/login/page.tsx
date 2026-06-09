"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { loginAdminSession, mfaChallenge } from "@/lib/api";
import { MfaEnrollDialog } from "@/components/mfa-enroll-dialog";
import { useAuthStore } from "@/store/authStore";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Eye, EyeOff } from "lucide-react";

// Only allow same-origin relative paths as `next` redirects so a malicious
// URL like /login?next=https://evil.example can't bounce the user off-site.
function sanitizeNextPath(next: string | null): string {
    if (!next) return "/dashboard";
    if (!next.startsWith("/") || next.startsWith("//")) return "/dashboard";
    return next;
}

// useSearchParams() must live under a Suspense boundary in Next.js 13+ —
// calling it in the page's default export would fail the production build
// with "useSearchParams() should be wrapped in a suspense boundary".
// Split into an inner client component + a Suspense-wrapped default export.
function LoginForm() {
    const router = useRouter();
    const searchParams = useSearchParams();
    const { setToken, setUser, setCsrfToken, scheduleRefresh } = useAuthStore();
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [mfaRequired, setMfaRequired] = useState(false);
    const [mfaToken, setMfaToken] = useState("");
    const [totpCode, setTotpCode] = useState("");
    // ADMIN_MFA_ENFORCED: account has no MFA yet — enrollToken is scoped to
    // the enroll/confirm endpoints only and expires in 15 minutes.
    const [enrollRequired, setEnrollRequired] = useState(false);
    const [enrollToken, setEnrollToken] = useState("");

    const _storeSessionAndRedirect = async (data: any) => {
        setToken(data.token);
        if (data.csrf_token) setCsrfToken(data.csrf_token);
        if (data.access_expires_at) scheduleRefresh(data.access_expires_at);
        setUser({
            id: data.user.id,
            email: data.user.email,
            role: data.user.role,
            first_name: data.user.first_name,
            last_name: data.user.last_name,
            modules: data.user.modules,
        });
        // Await the HttpOnly admin_token cookie being written before navigating.
        // router.push triggers middleware immediately; if the cookie isn't committed
        // yet the middleware redirects back to /login, causing an instant logout loop.
        // If the cookie write fails, surface an error instead of redirecting into a
        // broken session — auth succeeded but the session cannot be persisted.
        const cookieRes = await fetch('/api/auth/set-cookie', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: data.token }),
        });
        if (!cookieRes.ok) {
            throw new Error('Login succeeded but session could not be saved. Please try again.');
        }
        const next = sanitizeNextPath(searchParams.get("next"));
        router.push(next);
    };

    const handleMfaSubmit = async () => {
        setError("");
        setLoading(true);
        try {
            const data = await mfaChallenge(mfaToken, totpCode);
            await _storeSessionAndRedirect(data);
        } catch (e: any) {
            setError(e.message || "Invalid code. Please try again.");
        } finally {
            setLoading(false);
        }
    };

    const handleLogin = async () => {
        setError("");
        setLoading(true);
        try {
            const data = await loginAdminSession(email, password);

            if ("mfa_required" in data && data.mfa_required) {
                setMfaToken(data.mfa_token);
                setMfaRequired(true);
                return;
            }

            if ("mfa_enrollment_required" in data && data.mfa_enrollment_required) {
                setEnrollToken(data.mfa_token);
                setEnrollRequired(true);
                return;
            }

            await _storeSessionAndRedirect(data);
        } catch (e: any) {
            if (process.env.NODE_ENV === "development") {
                console.error('Login error:', e);
            }
            const status = e?.response?.status;
            if (status === 429) {
                setError("Too many login attempts. Please wait a minute and try again.");
            } else {
                setError(e.message || "Invalid credentials");
            }
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-md border-border/50">
                <CardHeader className="text-center">
                    <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-primary">
                        <span className="text-xl font-bold text-primary-foreground">S</span>
                    </div>
                    <CardTitle className="text-2xl font-bold">Spinr Admin</CardTitle>
                    <p className="text-sm text-muted-foreground mt-1">
                        Sign in to manage your rideshare platform
                    </p>
                </CardHeader>
                <CardContent className="space-y-4">
                    {error && (
                        <div className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
                            {error}
                        </div>
                    )}

                    {enrollRequired ? (
                        <>
                            <p className="text-sm text-muted-foreground">
                                Two-factor authentication is required for all staff accounts.
                                Set up your authenticator app to finish signing in.
                            </p>
                            <MfaEnrollDialog
                                open={enrollRequired}
                                onOpenChange={(open) => {
                                    if (!open) {
                                        // Dialog dismissed without finishing — drop the
                                        // enrollment token and return to the login form.
                                        setEnrollRequired(false);
                                        setEnrollToken("");
                                    }
                                }}
                                onEnrolled={() => {}}
                                authToken={enrollToken}
                                onSession={(data) => {
                                    _storeSessionAndRedirect(data).catch((e: any) =>
                                        setError(e.message || "Enrollment succeeded but sign-in failed. Please log in again.")
                                    );
                                }}
                            />
                            <button
                                type="button"
                                className="w-full text-sm text-muted-foreground hover:text-foreground"
                                onClick={() => { setEnrollRequired(false); setEnrollToken(""); setError(""); }}
                            >
                                Back to login
                            </button>
                        </>
                    ) : mfaRequired ? (
                        <>
                            <p className="text-sm text-muted-foreground">
                                Enter the 6-digit code from your authenticator app, or a backup code.
                            </p>
                            <div className="space-y-2">
                                <Label htmlFor="totp">Authentication Code</Label>
                                <Input
                                    id="totp"
                                    type="text"
                                    inputMode="numeric"
                                    placeholder="000000"
                                    maxLength={8}
                                    value={totpCode}
                                    onChange={(e) => setTotpCode(e.target.value.toUpperCase())}
                                    onKeyDown={(e) => e.key === "Enter" && handleMfaSubmit()}
                                    autoFocus
                                />
                            </div>
                            <Button
                                className="w-full"
                                onClick={handleMfaSubmit}
                                disabled={loading || totpCode.length < 6}
                            >
                                {loading ? "Verifying..." : "Verify"}
                            </Button>
                            <button
                                type="button"
                                className="w-full text-sm text-muted-foreground hover:text-foreground"
                                onClick={() => { setMfaRequired(false); setMfaToken(""); setTotpCode(""); setError(""); }}
                            >
                                Back to login
                            </button>
                        </>
                    ) : (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="email">Email</Label>
                                <Input
                                    id="email"
                                    type="email"
                                    placeholder="admin@example.com"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                                />
                            </div>

                            <div className="space-y-2">
                                <Label htmlFor="password">Password</Label>
                                <div className="relative">
                                    <Input
                                        id="password"
                                        type={showPassword ? "text" : "password"}
                                        placeholder="Enter your password"
                                        value={password}
                                        onChange={(e) => setPassword(e.target.value)}
                                        onKeyDown={(e) => e.key === "Enter" && handleLogin()}
                                        className="pr-10"
                                    />
                                    <button
                                        type="button"
                                        aria-label={showPassword ? "Hide password" : "Show password"}
                                        onClick={() => setShowPassword(!showPassword)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground focus:outline-none"
                                    >
                                        {showPassword ? (
                                            <EyeOff className="h-4 w-4" aria-hidden="true" />
                                        ) : (
                                            <Eye className="h-4 w-4" aria-hidden="true" />
                                        )}
                                    </button>
                                </div>
                            </div>

                            <Button
                                className="w-full"
                                onClick={handleLogin}
                                disabled={loading || !email || !password}
                            >
                                {loading ? "Signing in..." : "Sign In"}
                            </Button>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

export default function LoginPage() {
    return (
        <Suspense
            fallback={
                <div className="flex min-h-screen items-center justify-center bg-background p-4">
                    <div className="text-sm text-muted-foreground">Loading…</div>
                </div>
            }
        >
            <LoginForm />
        </Suspense>
    );
}