"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { FolderTree, Loader2, Plus } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    archiveCompanySection,
    assignMemberSection,
    createCompanySection,
    listCompanySections,
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

    const activeSections = sections.filter((s) => s.status === "active");

    return (
        <div className="space-y-6">
            <header>
                <h1 className="flex items-center gap-2 text-xl font-semibold">
                    <FolderTree className="h-5 w-5" /> Sections
                </h1>
                <p className="text-sm text-muted-foreground">
                    Organize your team into departments (showroom, service, …) — bookings
                    and reports filter by section. Budgets stay per-employee allowances.
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
                            <div
                                key={s.id}
                                className="flex items-center justify-between rounded-md border p-3"
                            >
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
