/**
 * /dashboard/documents — redirects to Service Areas.
 *
 * Document requirements are now managed per service area under the
 * "Documents" tab in Service Areas → expand area → Documents.
 * The global document_requirements table is no longer used for this purpose.
 */
import { redirect } from "next/navigation";

export default function DocumentRequirementsPage() {
    redirect("/dashboard/service-areas");
}
