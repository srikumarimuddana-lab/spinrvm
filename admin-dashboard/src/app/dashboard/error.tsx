"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function DashboardError({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    useEffect(() => {
        // Log to Sentry/console so the error is surfaced even when the UI absorbs it.
        console.error("[DashboardError]", error);
    }, [error]);

    return (
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 p-8">
            <div className="flex items-center gap-3 text-destructive">
                <AlertTriangle className="h-8 w-8" />
                <h2 className="text-xl font-semibold">Something went wrong</h2>
            </div>
            <p className="text-muted-foreground text-center max-w-md">
                An unexpected error occurred while loading this page. The error has been
                logged. You can try reloading the page or navigating away and back.
            </p>
            {process.env.NODE_ENV !== "production" && (
                <pre className="text-xs text-destructive bg-destructive/10 rounded p-4 max-w-lg overflow-auto">
                    {error.message}
                    {error.digest ? `\ndigest: ${error.digest}` : ""}
                </pre>
            )}
            <button
                onClick={reset}
                className="flex items-center gap-2 px-4 py-2 rounded-md bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
                <RefreshCw className="h-4 w-4" />
                Try again
            </button>
        </div>
    );
}
