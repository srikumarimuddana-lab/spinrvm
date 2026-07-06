"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import type {
    CorporateMember,
    CorporateMemberRole,
    CorporateMemberStatus,
} from "@/lib/api";
import {
    inviteCompanyMember,
    listCompanyMembers,
    updateCompanyMember,
} from "@/lib/companyApi";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import {
    Table,
    TableBody,
    TableCell,
    TableHead,
    TableHeader,
    TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Mail, PauseCircle, PlayCircle, UserPlus } from "lucide-react";

const STATUS_COLORS: Record<CorporateMemberStatus, string> = {
    invited: "bg-yellow-100 text-yellow-800",
    active: "bg-emerald-100 text-emerald-800",
    suspended: "bg-orange-100 text-orange-800",
    removed: "bg-gray-200 text-gray-600",
};

type Filter = "all" | CorporateMemberStatus;

export default function MembersPage() {
    const { id } = useParams<{ id: string }>();
    const [members, setMembers] = useState<CorporateMember[]>([]);
    const [filter, setFilter] = useState<Filter>("all");
    const [loading, setLoading] = useState(true);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<CorporateMemberRole>("member");
    const [inviting, setInviting] = useState(false);
    const [feedback, setFeedback] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        if (!id) return;
        setLoading(true);
        try {
            const rows = await listCompanyMembers(id);
            setMembers(rows);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Failed to load");
        } finally {
            setLoading(false);
        }
    }, [id]);

    useEffect(() => {
        load();
    }, [load]);

    const filtered = members.filter(
        (m) => filter === "all" || m.status === filter
    );
    const { sorted, sort, toggle } = useTableSort(filtered);

    const onInvite = async () => {
        if (!id || !inviteEmail.trim()) return;
        setInviting(true);
        setError(null);
        setFeedback(null);
        try {
            const res = await inviteCompanyMember(id, {
                email: inviteEmail.trim(),
                role: inviteRole,
            });
            setFeedback(`Invite sent — share: ${res.invite_url}`);
            setInviteEmail("");
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Invite failed");
        } finally {
            setInviting(false);
        }
    };

    const toggleStatus = async (m: CorporateMember) => {
        if (!id) return;
        const target: CorporateMemberStatus =
            m.status === "suspended" ? "active" : "suspended";
        try {
            await updateCompanyMember(id, m.id, { status: target });
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Update failed");
        }
    };

    return (
        <div className="space-y-6">
            <header className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-semibold">Members</h1>
                    <p className="text-muted-foreground">
                        Invite, suspend, and reactivate members.
                    </p>
                </div>
            </header>

            <Card>
                <CardContent className="space-y-3 p-4">
                    <div className="grid gap-3 sm:grid-cols-[1fr_150px_auto]">
                        <div>
                            <Label htmlFor="invite-email">Invite by email</Label>
                            <Input
                                id="invite-email"
                                type="email"
                                placeholder="name@company.com"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                            />
                        </div>
                        <div>
                            <Label>Role</Label>
                            <Select
                                value={inviteRole}
                                onValueChange={(v) => setInviteRole(v as CorporateMemberRole)}
                            >
                                <SelectTrigger>
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">Member</SelectItem>
                                    <SelectItem value="admin">Admin</SelectItem>
                                    <SelectItem value="owner">Owner</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <div className="flex items-end">
                            <Button onClick={onInvite} disabled={inviting || !inviteEmail.trim()}>
                                <UserPlus className="mr-2 h-4 w-4" />
                                {inviting ? "Sending…" : "Send invite"}
                            </Button>
                        </div>
                    </div>
                    {feedback && (
                        <p className="rounded bg-emerald-50 p-2 text-xs text-emerald-800">
                            {feedback}
                        </p>
                    )}
                    {error && (
                        <p className="rounded bg-red-50 p-2 text-xs text-red-700">{error}</p>
                    )}
                </CardContent>
            </Card>

            <div className="flex gap-2">
                {(["all", "active", "invited", "suspended", "removed"] as Filter[]).map(
                    (f) => (
                        <Button
                            key={f}
                            size="sm"
                            variant={filter === f ? "default" : "outline"}
                            onClick={() => setFilter(f)}
                        >
                            {f}
                        </Button>
                    )
                )}
            </div>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="invited_email" sort={sort} onSort={toggle}>Email</SortableHead>
                                <SortableHead column="role" sort={sort} onSort={toggle}>Role</SortableHead>
                                <SortableHead column="status" sort={sort} onSort={toggle}>Status</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading && (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            )}
                            {!loading && filtered.length === 0 && (
                                <TableRow>
                                    <TableCell colSpan={4} className="text-center text-muted-foreground">
                                        No members.
                                    </TableCell>
                                </TableRow>
                            )}
                            {sorted.map((m) => (
                                <TableRow key={m.id}>
                                    <TableCell className="flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 text-muted-foreground" />
                                        {m.invited_email || m.user_id || m.id}
                                    </TableCell>
                                    <TableCell className="capitalize">{m.role}</TableCell>
                                    <TableCell>
                                        <Badge className={STATUS_COLORS[m.status]}>
                                            {m.status}
                                        </Badge>
                                    </TableCell>
                                    <TableCell className="text-right">
                                        {m.status === "active" && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={() => toggleStatus(m)}
                                            >
                                                <PauseCircle className="mr-1 h-3.5 w-3.5" />
                                                Suspend
                                            </Button>
                                        )}
                                        {m.status === "suspended" && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                onClick={() => toggleStatus(m)}
                                            >
                                                <PlayCircle className="mr-1 h-3.5 w-3.5" />
                                                Reactivate
                                            </Button>
                                        )}
                                    </TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}
