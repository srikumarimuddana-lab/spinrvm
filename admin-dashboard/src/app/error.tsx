"use client";

import { useEffect } from "react";
import { Button } from "@/components/ui/button";

export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    console.error("Unhandled app error:", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 text-center">
      <h2 className="text-2xl font-bold">Something went wrong</h2>
      <p className="text-muted-foreground max-w-md">{error.message || "An unexpected error occurred. Please try again."}</p>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
