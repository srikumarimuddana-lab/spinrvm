"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { FileQuestion } from "lucide-react";

const DEFAULT_MESSAGE = "The page you are looking for does not exist or has been moved.";

// Public visitors can't use the staff dashboard: "/dashboard" bounces them to
// the staff login. Send each public audience to the page that serves it.
function actionFor(pathname: string | null): { message: string; href?: string; label?: string } {
    if (pathname?.startsWith("/track/")) {
        return { message: "This tracking link is invalid or has expired. Ask the rider to share it again." };
    }
    if (pathname?.startsWith("/register/")) {
        return { message: DEFAULT_MESSAGE, href: "/register/driver", label: "Go to driver sign-up" };
    }
    if (pathname?.startsWith("/company-portal/")) {
        return { message: DEFAULT_MESSAGE, href: "/company-portal", label: "Go to company portal" };
    }
    return { message: DEFAULT_MESSAGE, href: "/dashboard", label: "Go to Dashboard" };
}

export default function NotFound() {
    const { message, href, label } = actionFor(usePathname());
    return (
        <div className="min-h-screen flex items-center justify-center bg-background p-4">
            <Card className="max-w-md w-full">
                <CardContent className="pt-8 pb-8 text-center space-y-4">
                    <FileQuestion className="h-16 w-16 text-muted-foreground mx-auto" />
                    <div>
                        <h1 className="text-2xl font-bold tracking-tight">Page Not Found</h1>
                        <p className="text-muted-foreground mt-2">{message}</p>
                    </div>
                    {href && (
                        <Button asChild>
                            <Link href={href}>{label}</Link>
                        </Button>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}
