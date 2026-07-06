"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useCompanyAuthStore } from "@/store/companyAuthStore";

/**
 * Company-portal root gate — bound to the COMPANY session (rider-token
 * companyAuthStore), never the staff admin session. The edge middleware
 * already bounces cookie-less visits to /company-login; this client gate
 * covers in-app state (logout, expired rehydrated session) and restores a
 * rehydrated session's access token via silent refresh.
 */
export default function CompanyPortalRootLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const router = useRouter();
    const { isAuthenticated, isLoading, token, silentRefresh, logout } = useCompanyAuthStore();

    // Rehydrated session (profile persisted, token memory-only): restore the
    // access token from the HttpOnly refresh cookie before rendering.
    useEffect(() => {
        if (!isLoading && isAuthenticated && !token) {
            silentRefresh().then((ok) => {
                if (!ok) {
                    logout().then(() => router.replace("/company-login?next=/company-portal"));
                }
            });
        }
    }, [isAuthenticated, isLoading, token, silentRefresh, logout, router]);

    useEffect(() => {
        if (!isLoading && !isAuthenticated) {
            router.replace("/company-login?next=/company-portal");
        }
    }, [isAuthenticated, isLoading, router]);

    if (isLoading) {
        return (
            <div className="flex items-center justify-center min-h-screen bg-background">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
            </div>
        );
    }

    if (!isAuthenticated) return null;

    return <div className="min-h-screen bg-background">{children}</div>;
}
