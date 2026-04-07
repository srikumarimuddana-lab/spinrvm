"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FileQuestion } from "lucide-react";

export default function NotFound() {
    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <Card className="max-w-md w-full">
                <CardContent className="pt-8 pb-8 text-center space-y-4">
                    <FileQuestion className="h-16 w-16 text-muted-foreground mx-auto" />
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Page Not Found</h1>
                        <p className="text-muted-foreground mt-2">
                            The page you are looking for does not exist or has been moved.
                        </p>
                    </div>
                    <Button asChild>
                        <Link href="/dashboard">Go to Dashboard</Link>
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
