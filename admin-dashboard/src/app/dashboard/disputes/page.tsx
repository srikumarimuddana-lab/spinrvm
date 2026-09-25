"use client";

import { PageHeader } from "@/components/page-header";
import { AlertTriangle } from "lucide-react";
import { useRequireModule } from "@/hooks/useRequireModule";
import ChargebacksTab from "./chargebacks-tab";

// In-app disputes are disabled (owner decision 2026-09-25): riders and
// drivers with a question about a charge email support@spinr.ca (a Zoho Desk
// ticket) or go to their card issuer. The old "Rider Disputes" tab (list +
// resolve/refund dialog) is gone; the backend answers 410 on create/resolve.
// Card-network chargebacks are the only dispute surface left here. The route
// stays /dashboard/disputes (and /dashboard/support?tab=disputes) so
// bookmarks and the next.config.ts redirect keep working. See
// docs/change-log/2026-09-25-disable-in-app-disputes.md.
export default function DisputesPage() {
  const { allowed } = useRequireModule("support");

  if (!allowed) return null;

  return (
    <div className="space-y-6">
      <PageHeader
        title={
          <span className="inline-flex items-center gap-2">
            <AlertTriangle className="h-6 w-6 text-warning" />
            Chargebacks
          </span>
        }
        description="Card-issuer chargebacks from Stripe. Spinr has no in-app dispute option: charge questions go to support@spinr.ca."
      />

      <ChargebacksTab />
    </div>
  );
}
