"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { loginAdminSession } from "@/lib/api";
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
    const { setToken, setUser } = useAuthStore();
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");
    const [showPassword, setShowPassword] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");

    const handleLogin = async () => {
        setError("");
        setLoading(true);
        try {
            // Call the admin login API
            const data = await loginAdminSession(email, password);

            // Store token and user in Zustand. setToken also dual-writes
            // the `admin_token` cookie so src/middleware.ts will let the
            // next navigation through to /dashboard.
            setToken(data.token);
            setUser({
                id: data.user.id,
                email: data.user.email,
                role: data.user.role,
            });

            // Bounce back to the originally requested page if middleware
            // redirected the user here, otherwise land on /dashboard.
            const next = sanitizeNextPath(searchParams.get("next"));
            router.push(next);
        } catch (e: any) {
            console.error('Login error:', e);
            setError(e.message || "Invalid credentials");
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