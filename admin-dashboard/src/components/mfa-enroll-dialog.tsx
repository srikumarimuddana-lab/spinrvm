"use client";

import { useState } from "react";
import { mfaEnroll, mfaConfirm } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
    DialogDescription,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/use-toast";

interface Props {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    onEnrolled: () => void;
}

type Step = "setup" | "backup-codes";

export function MfaEnrollDialog({ open, onOpenChange, onEnrolled }: Props) {
    const { toast } = useToast();
    const [step, setStep] = useState<Step>("setup");
    const [secret, setSecret] = useState("");
    const [otpauthUri, setOtpauthUri] = useState("");
    const [totpCode, setTotpCode] = useState("");
    const [backupCodes, setBackupCodes] = useState<string[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState("");
    const [enrollStarted, setEnrollStarted] = useState(false);

    const startEnroll = async () => {
        setLoading(true);
        setError("");
        try {
            const data = await mfaEnroll();
            setSecret(data.secret);
            setOtpauthUri(data.otpauth_uri);
            setEnrollStarted(true);
        } catch (e: any) {
            setError(e.message || "Failed to start enrollment");
        } finally {
            setLoading(false);
        }
    };

    const handleConfirm = async () => {
        setLoading(true);
        setError("");
        try {
            const data = await mfaConfirm(totpCode);
            setBackupCodes(data.backup_codes);
            setStep("backup-codes");
        } catch (e: any) {
            setError(e.message || "Invalid code. Please try again.");
        } finally {
            setLoading(false);
        }
    };

    const handleDone = () => {
        toast({ title: "MFA enabled", description: "Two-factor authentication is now active on your account." });
        onEnrolled();
        onOpenChange(false);
        setStep("setup");
        setSecret("");
        setOtpauthUri("");
        setTotpCode("");
        setBackupCodes([]);
        setEnrollStarted(false);
    };

    const handleOpenChange = (val: boolean) => {
        if (!val) {
            setStep("setup");
            setSecret("");
            setOtpauthUri("");
            setTotpCode("");
            setBackupCodes([]);
            setEnrollStarted(false);
            setError("");
        }
        onOpenChange(val);
    };

    return (
        <Dialog open={open} onOpenChange={handleOpenChange}>
            <DialogContent className="max-w-md">
                {step === "setup" ? (
                    <>
                        <DialogHeader>
                            <DialogTitle>Enable Two-Factor Authentication</DialogTitle>
                            <DialogDescription>
                                Use an authenticator app (Google Authenticator, Authy, etc.) to scan the key below.
                            </DialogDescription>
                        </DialogHeader>

                        {error && (
                            <div className="rounded-lg bg-destructive/10 p-3 text-sm text-destructive">
                                {error}
                            </div>
                        )}

                        {!enrollStarted ? (
                            <Button onClick={startEnroll} disabled={loading} className="w-full">
                                {loading ? "Loading…" : "Get Setup Key"}
                            </Button>
                        ) : (
                            <div className="space-y-4">
                                <div className="space-y-2">
                                    <Label>Manual Entry Key</Label>
                                    <div className="rounded-md bg-muted px-3 py-2 font-mono text-sm tracking-widest select-all break-all">
                                        {secret}
                                    </div>
                                </div>

                                <div className="space-y-1">
                                    <Label>Or open in your authenticator app</Label>
                                    <a
                                        href={otpauthUri}
                                        className="block truncate text-xs text-primary underline-offset-4 hover:underline"
                                    >
                                        Open authenticator link
                                    </a>
                                </div>

                                <div className="space-y-2">
                                    <Label htmlFor="totp-confirm">Enter the 6-digit code to confirm</Label>
                                    <Input
                                        id="totp-confirm"
                                        type="text"
                                        inputMode="numeric"
                                        placeholder="000000"
                                        maxLength={6}
                                        value={totpCode}
                                        onChange={(e) => setTotpCode(e.target.value)}
                                        onKeyDown={(e) => e.key === "Enter" && handleConfirm()}
                                        autoFocus
                                    />
                                </div>

                                <Button
                                    onClick={handleConfirm}
                                    disabled={loading || totpCode.length < 6}
                                    className="w-full"
                                >
                                    {loading ? "Verifying…" : "Enable MFA"}
                                </Button>
                            </div>
                        )}
                    </>
                ) : (
                    <>
                        <DialogHeader>
                            <DialogTitle>Save Your Backup Codes</DialogTitle>
                            <DialogDescription>
                                Store these codes somewhere safe. Each code can be used once if you lose access to your authenticator app.
                            </DialogDescription>
                        </DialogHeader>

                        <div className="grid grid-cols-2 gap-2 rounded-md bg-muted p-3 font-mono text-sm">
                            {backupCodes.map((code) => (
                                <span key={code} className="select-all">{code}</span>
                            ))}
                        </div>

                        <p className="text-xs text-muted-foreground">
                            These codes will not be shown again. Copy or print them now.
                        </p>

                        <Button onClick={handleDone} className="w-full">
                            I&apos;ve saved my codes
                        </Button>
                    </>
                )}
            </DialogContent>
        </Dialog>
    );
}
