"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AlertTriangle } from "lucide-react";

export default function Error({
    error,
    reset,
}: {
    error: Error & { digest?: string };
    reset: () => void;
}) {
    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <Card className="max-w-md w-full">
                <CardContent className="pt-8 pb-8 text-center space-y-4">
                    <AlertTriangle className="h-16 w-16 text-red-500 mx-auto" />
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Something Went Wrong</h1>
                        <p className="text-muted-foreground mt-2">
                            {error.message || "An unexpected error occurred. Please try again."}
                        </p>
                    </div>
                    <div className="flex gap-3 justify-center">
                        <Button onClick={reset}>Try Again</Button>
                        <Button variant="outline" asChild>
                            <Link href="/dashboard">Go to Dashboard</Link>
                        </Button>
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
