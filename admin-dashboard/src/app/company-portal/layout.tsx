"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useCompanyAuthStore } from "@/store/companyAuthStore";

/**
 * Company-portal root gate — bound to the COMPANY session (rider-token
 * companyAuthStore), never the staff admin session. The edge middleware
 * already lets the shell through when a refreshable session (company_session
 * marker) exists; this client gate then restores the access token from the
 * HttpOnly refresh cookie BEFORE deciding to redirect.
 */
export default function CompanyPortalRootLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const router = useRouter();
    const { isAuthenticated, isLoading, token, silentRefresh, logout } = useCompanyAuthStore();
    const attempted = useRef(false);
    const [checking, setChecking] = useState(true);

    // Whenever we lack a live authenticated token, try silentRefresh FIRST and
    // only redirect if it fails. This covers BOTH a rehydrated session
    // (isAuthenticated, token dropped from memory) AND a fresh tab / bookmark
    // where sessionStorage is empty (isAuthenticated=false) but the 30-day
    // refresh cookie the middleware allowed through is still valid — otherwise
    // that valid session would be treated as logged out.
    useEffect(() => {
        if (isLoading) return;
        if (isAuthenticated && token) {
            setChecking(false);
            return;
        }
        if (attempted.current) return;
        attempted.current = true;
        silentRefresh().then((ok) => {
            if (ok) {
                setChecking(false);
            } else {
                logout().then(() => router.replace("/company-login?next=/company-portal"));
            }
        });
    }, [isAuthenticated, isLoading, token, silentRefresh, logout, router]);

    if (isLoading || checking) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-background">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
            </div>
        );
    }

    if (!isAuthenticated) return null;

    return <div className="min-h-screen bg-background">{children}</div>;
}
