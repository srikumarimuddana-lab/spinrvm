"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";
import { Button } from "@/components/ui/button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Unhandled app error:", error);
    // React error boundaries swallow the error after rendering the fallback —
    // Sentry never sees it unless it is reported explicitly here.
    Sentry.captureException(error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 text-center">
      <h2 className="text-2xl font-bold">Something went wrong</h2>
      <p className="text-muted-foreground max-w-md">{error.message || "An unexpected error occurred. Please try again."}</p>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
