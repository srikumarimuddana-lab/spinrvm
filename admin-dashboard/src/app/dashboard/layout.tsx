import { Sidebar } from "@/components/sidebar";
import { RequireAuth } from "@/lib/auth";

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <RequireAuth>
            <div className="min-h-screen bg-background">
                <Sidebar />
                <main className="md:ml-64">
                    <div className="p-6 md:p-8">{children}</div>
                </main>
            </div>
        </RequireAuth>
    );
}
