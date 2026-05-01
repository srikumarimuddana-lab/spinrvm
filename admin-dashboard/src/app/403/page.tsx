"use client";

import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ShieldOff } from "lucide-react";

export default function ForbiddenPage() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-background p-4">
      <Card className="max-w-md w-full">
        <CardContent className="pt-8 pb-8 text-center space-y-4">
          <ShieldOff className="h-16 w-16 text-destructive mx-auto" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Access Denied</h1>
            <p className="text-muted-foreground mt-2">
              You do not have permission to view this page.
              Contact your administrator to request access.
            </p>
          </div>
          <Button asChild variant="outline">
            <Link href="/dashboard">Go to Dashboard</Link>
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
