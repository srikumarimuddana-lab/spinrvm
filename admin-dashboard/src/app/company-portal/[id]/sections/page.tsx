"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { CreditCard, FolderTree, Loader2, Plus, Wallet } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    allocateSectionFunds,
    archiveCompanySection,
    assignMemberSection,
    createCompanySection,
    createSectionTopupSession,
    getSectionWallet,
    listCompanySections,
    updateCompanySection,
    CompanySection,
} from "@/lib/companyApi";
import type {
    CorporateMember,
} from "@/lib/api";
import {
    listCompanyMembers,
} from "@/lib/companyApi";

/**
 * Sections (departments) — grouping + reporting only; budgets stay
 * per-member allowances. Owner/admin manage sections and assign members
 * here; the bookings list filters by section.
 */
export default function CompanySectionsPage() {
    const params = useParams();
    const companyId = typeof params?.id === "string" ? params.id : "";

    const [sections, setSections] = useState<CompanySection[]>([]);
    const [members, setMembers] = useState<(CorporateMember & { section_id?: string | null })[]>([]);
    const [newName, setNewName] = useState("");
    const [busy, setBusy] = useState(false);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!companyId) return;
        try {
            const [sec, mem] = await Promise.all([
                listCompanySections(companyId),
                listCompanyMembers(companyId, "active"),
            ]);
            setSections(sec.sections);
            setMembers(mem as (CorporateMember & { section_id?: string | null })[]);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load sections");
        } finally {
            setLoading(false);
        }
    }, [companyId]);

    useEffect(() => {
        load();
    }, [load]);

    const handleCreate = async () => {
        if (!newName.trim()) return;
        setBusy(true);
        setError(null);
        try {
            await createCompanySection(companyId, { name: newName.trim() });
            setNewName("");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not create section");
        } finally {
            setBusy(false);
        }
    };

    const handleArchive = async (sectionId: string) => {
        if (!window.confirm("Archive this section? Members keep their history; you can reassign them anytime.")) {
            return;
        }
        try {
            await archiveCompanySection(companyId, sectionId);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not archive section");
        }
    };

    const handleAssign = async (memberId: string, sectionId: string) => {
        try {
            await assignMemberSection(companyId, memberId, sectionId || null);
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not assign section");
        }
    };

    // M5.7: per-section wallet balances (departmental funding).
    const [walletBalances, setWalletBalances] = useState<Record<string, string>>({});
    useEffect(() => {
        if (!companyId || sections.length === 0) return;
        let cancelled = false;
        (async () => {
            const entries: Record<string, string> = {};
            await Promise.all(
                sections
                    .filter((s) => s.status === "active")
                    .map(async (s) => {
                        try {
                            const info = await getSectionWallet(companyId, s.id);
                            entries[s.id] = String(info.wallet?.balance ?? "0");
                        } catch {
                            /* non-heads without access simply see no balance */
                        }
                    })
            );
            if (!cancelled) setWalletBalances(entries);
        })();
        return () => {
            cancelled = true;
        };
    }, [companyId, sections]);

    const handleSetHead = async (sectionId: string, memberId: string) => {
        try {
            await updateCompanySection(companyId, sectionId, { head_member_id: memberId });
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not set section head");
        }
    };

    const handleAllocate = async (sectionId: string) => {
        const raw = window.prompt("Amount to allocate from the master wallet (CAD):");
        if (!raw) return;
        setBusy(true);
        setError(null);
        try {
            await allocateSectionFunds(companyId, {
                section_id: sectionId,
                amount: raw.trim(),
                client_key: crypto.randomUUID(),
            });
            const info = await getSectionWallet(companyId, sectionId).catch(() => null);
            if (info?.wallet) {
                setWalletBalances((b) => ({ ...b, [sectionId]: String(info.wallet!.balance) }));
            }
        } catch (e) {
            setError(e instanceof Error ? e.message : "Allocation failed");
        } finally {
            setBusy(false);
        }
    };

    const handleTopup = async (sectionId: string) => {
        const raw = window.prompt("Top-up amount via Stripe (CAD, $100-$10,000; $5,000/day cap):");
        if (!raw) return;
        setBusy(true);
        setError(null);
        try {
            const { checkout_url } = await createSectionTopupSession(
                companyId,
                sectionId,
                raw.trim(),
                crypto.randomUUID()
            );
            window.location.assign(checkout_url);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not start the top-up");
            setBusy(false);
        }
    };

    const activeSections = sections.filter((s) => s.status === "active");

    return (
        <div className="space-y-6">
            <header>
                <h1 className="flex items-center gap-2 text-xl font-semibold">
                    <FolderTree className="h-5 w-5" /> Sections
                </h1>
                <p className="text-sm text-muted-foreground">
                    Organize your team into departments (showroom, service, …) — bookings
                    and reports filter by section. Each department can hold its own
                    wallet, funded by allocation or its head&apos;s Stripe top-up.
                </p>
            </header>

            {error && <p className="rounded bg-red-50 p-3 text-sm text-red-700">{error}</p>}
            {loading && <p className="text-sm text-muted-foreground">Loading…</p>}

            <Card>
                <CardContent className="space-y-4 p-5">
                    <div className="flex gap-2">
                        <Input
                            placeholder="New section name (e.g. Service Department)"
                            value={newName}
                            maxLength={80}
                            onChange={(e) => setNewName(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && !busy && handleCreate()}
                        />
                        <Button onClick={handleCreate} disabled={busy || !newName.trim()}>
                            {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
                            <span className="ml-1">Add</span>
                        </Button>
                    </div>

                    <div className="space-y-2">
                        {sections.map((s) => (
                            <div key={s.id} className="rounded-md border p-3">
                                <div className="flex items-center justify-between">
                                    <div>
                                        <span className="font-medium">{s.name}</span>
                                        <span className="ml-2 text-xs text-muted-foreground">
                                            {s.member_count ?? 0} member{(s.member_count ?? 0) === 1 ? "" : "s"}
                                        </span>
                                        {s.status === "archived" && (
                                            <Badge className="ml-2 bg-muted text-muted-foreground hover:bg-muted">
                                                archived
                                            </Badge>
                                        )}
                                    </div>
                                    {s.status === "active" && (
                                        <Button variant="ghost" size="sm" onClick={() => handleArchive(s.id)}>
                                            Archive
                                        </Button>
                                    )}
                                </div>
                                {s.status === "active" && (
                                    <div className="mt-2 flex flex-wrap items-center gap-3 text-sm">
                                        <span className="inline-flex items-center gap-1 text-muted-foreground">
                                            <Wallet className="h-3.5 w-3.5" />
                                            {walletBalances[s.id] !== undefined
                                                ? `$${walletBalances[s.id]}`
                                                : "\u2014"}
                                        </span>
                                        <label className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                            Head:
                                            <select
                                                className="h-7 rounded-md border border-input bg-background px-1 text-xs"
                                                value={s.head_member_id ?? ""}
                                                onChange={(e) => handleSetHead(s.id, e.target.value)}
                                            >
                                                <option value="">None</option>
                                                {members.map((m) => (
                                                    <option key={m.id} value={m.id}>
                                                        {m.invited_email ?? m.id}
                                                    </option>
                                                ))}
                                            </select>
                                        </label>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={busy}
                                            onClick={() => handleAllocate(s.id)}
                                        >
                                            <Wallet className="mr-1 h-3.5 w-3.5" /> Allocate
                                        </Button>
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={busy}
                                            onClick={() => handleTopup(s.id)}
                                        >
                                            <CreditCard className="mr-1 h-3.5 w-3.5" /> Top up
                                        </Button>
                                    </div>
                                )}
                            </div>
                        ))}
                        {!loading && sections.length === 0 && (
                            <p className="py-4 text-center text-sm text-muted-foreground">
                                No sections yet — add your first above.
                            </p>
                        )}
                    </div>
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-5">
                    <h2 className="mb-3 text-sm font-semibold">Member assignments</h2>
                    <div className="space-y-2">
                        {members.map((m) => (
                            <div
                                key={m.id}
                                className="flex items-center justify-between gap-3 rounded-md border p-3"
                            >
                                <div className="min-w-0">
                                    <div className="truncate text-sm font-medium">
                                        {m.invited_email ?? m.user_id ?? m.id}
                                    </div>
                                    <div className="text-xs text-muted-foreground">{m.role}</div>
                                </div>
                                <select
                                    className="h-8 rounded-md border border-input bg-background px-2 text-sm"
                                    value={m.section_id ?? ""}
                                    onChange={(e) => handleAssign(m.id, e.target.value)}
                                >
                                    <option value="">No section</option>
                                    {activeSections.map((s) => (
                                        <option key={s.id} value={s.id}>
                                            {s.name}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        ))}
                        {!loading && members.length === 0 && (
                            <p className="py-4 text-center text-sm text-muted-foreground">
                                No active members yet.
                            </p>
                        )}
                    </div>
                </CardContent>
            </Card>
        </div>
    );
}
