"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { sendOtp, loginAdmin } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export default function LoginPage() {
    const router = useRouter();
    const [phone, setPhone] = useState("");
    const [code, setCode] = useState("");
    const [step, setStep] = useState<"phone" | "otp">("phone");
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [devOtp, setDevOtp] = useState("");

    const handleSendOtp = async () => {
        setError("");
        setLoading(true);
        try {
            const res = await sendOtp(phone);
            if (res.dev_otp) setDevOtp(res.dev_otp);
            setStep("otp");
        } catch (e: any) {
            setError(e.message || "Failed to send OTP");
        } finally {
            setLoading(false);
        }
    };

    const handleVerify = async () => {
        setError("");
        setLoading(true);
        try {
            const res = await loginAdmin(phone, code);
            localStorage.setItem("admin_token", res.token);
            router.push("/dashboard");
        } catch (e: any) {
            setError(e.message || "Invalid code");
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="flex min-h-screen items-center justify-center bg-background p-4">
            <Card className="w-full max-w-md border-border/50">
                <CardHeader className="text-center">
                    <div className="mx-auto mb-4 flex h-14 w-14 items-center justify-center rounded-xl bg-gradient-to-br from-violet-600 to-indigo-600">
                        <span className="text-xl font-bold text-white">S</span>
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

                    {step === "phone" ? (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="phone">Phone Number</Label>
                                <Input
                                    id="phone"
                                    placeholder="+1306555xxxx"
                                    value={phone}
                                    onChange={(e) => setPhone(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleSendOtp()}
                                />
                            </div>
                            <Button
                                className="w-full"
                                onClick={handleSendOtp}
                                disabled={loading || !phone}
                            >
                                {loading ? "Sending..." : "Send Verification Code"}
                            </Button>
                        </>
                    ) : (
                        <>
                            <div className="space-y-2">
                                <Label htmlFor="code">Verification Code</Label>
                                <Input
                                    id="code"
                                    placeholder="Enter 4-digit code"
                                    value={code}
                                    onChange={(e) => setCode(e.target.value)}
                                    onKeyDown={(e) => e.key === "Enter" && handleVerify()}
                                    maxLength={4}
                                />
                                {devOtp && (
                                    <p className="text-xs text-muted-foreground">
                                        Dev OTP: <span className="font-mono font-bold text-emerald-500">{devOtp}</span>
                                    </p>
                                )}
                            </div>
                            <Button
                                className="w-full"
                                onClick={handleVerify}
                                disabled={loading || !code}
                            >
                                {loading ? "Verifying..." : "Sign In"}
                            </Button>
                            <Button
                                variant="ghost"
                                className="w-full"
                                onClick={() => {
                                    setStep("phone");
                                    setCode("");
                                    setDevOtp("");
                                }}
                            >
                                Change Phone Number
                            </Button>
                        </>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
