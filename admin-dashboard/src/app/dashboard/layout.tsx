import { Sidebar } from "@/components/sidebar";

export default function DashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    return (
        <div className="min-h-screen bg-background">
            <Sidebar />
            <main className="md:ml-64">
                <div className="p-6 md:p-8">{children}</div>
            </main>
        </div>
    );
}
