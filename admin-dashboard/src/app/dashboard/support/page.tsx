"use client";

import { useState } from "react";
import { LifeBuoy, HelpCircle, PackageSearch, Flag } from "lucide-react";
import dynamic from "next/dynamic";

const TicketsTab = dynamic(() => import("./_tabs/tickets"), { ssr: false, loading: () => <TabLoader /> });
const DisputesTab = dynamic(() => import("./_tabs/disputes"), { ssr: false, loading: () => <TabLoader /> });
const LostAndFoundTab = dynamic(() => import("./_tabs/lost-and-found"), { ssr: false, loading: () => <TabLoader /> });
const FlagsTab = dynamic(() => import("./_tabs/flags"), { ssr: false, loading: () => <TabLoader /> });

const TABS = [
    { id: "tickets", label: "Tickets", icon: LifeBuoy },
    { id: "disputes", label: "Disputes", icon: HelpCircle },
    { id: "lost-found", label: "Lost & Found", icon: PackageSearch },
    { id: "flags", label: "Flags", icon: Flag },
];

function TabLoader() {
    return <div className="flex items-center justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
}

export default function SupportPage() {
    const [activeTab, setActiveTab] = useState("tickets");

    return (
        <div>
            <div className="mb-4">
                <h1 className="text-2xl font-bold flex items-center gap-2"><LifeBuoy className="h-6 w-6" /> Support & Issues</h1>
                <p className="text-sm text-muted-foreground mt-1">Manage tickets, disputes, lost items, and user flags</p>
            </div>

            <div className="flex gap-1 mb-4 overflow-x-auto border-b">
                {TABS.map(tab => (
                    <button key={tab.id} onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium whitespace-nowrap border-b-2 transition-colors ${
                            activeTab === tab.id
                                ? "border-primary text-primary"
                                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
                        }`}>
                        <tab.icon className="h-4 w-4" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {activeTab === "tickets" && <TicketsTab />}
            {activeTab === "disputes" && <DisputesTab />}
            {activeTab === "lost-found" && <LostAndFoundTab />}
            {activeTab === "flags" && <FlagsTab />}
        </div>
    );
}
