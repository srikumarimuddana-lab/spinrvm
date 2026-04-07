"use client";

import { useState } from "react";
import { DollarSign, CreditCard, Flame, Banknote } from "lucide-react";
import dynamic from "next/dynamic";

const FareConfigTab = dynamic(() => import("./_tabs/fare-config"), { ssr: false, loading: () => <TabLoader /> });
const SurgeTab = dynamic(() => import("./_tabs/surge"), { ssr: false, loading: () => <TabLoader /> });
const SpinrPassTab = dynamic(() => import("./_tabs/spinr-pass"), { ssr: false, loading: () => <TabLoader /> });

const TABS = [
    { id: "fares", label: "Fare Configuration", icon: Banknote },
    { id: "surge", label: "Surge Pricing", icon: Flame },
    { id: "spinr-pass", label: "Spinr Pass", icon: CreditCard },
];

function TabLoader() {
    return <div className="flex items-center justify-center py-20"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;
}

export default function PricingPage() {
    const [activeTab, setActiveTab] = useState("fares");

    return (
        <div>
            <div className="mb-4">
                <h1 className="text-2xl font-bold flex items-center gap-2"><DollarSign className="h-6 w-6" /> Pricing & Billing</h1>
                <p className="text-sm text-muted-foreground mt-1">Manage fare configurations, surge pricing, and driver subscription plans</p>
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

            {activeTab === "fares" && <FareConfigTab />}
            {activeTab === "surge" && <SurgeTab />}
            {activeTab === "spinr-pass" && <SpinrPassTab />}
        </div>
    );
}
