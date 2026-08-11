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
import { Copy, Mail, PauseCircle, PlayCircle, RefreshCw, UserPlus } from "lucide-react";

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
    const [feedback, setFeedback] = useState<{
        kind: "ok" | "warn";
        text: string;
        link?: string | null;
    } | null>(null);
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

    const sendInvite = async (email: string, role: CorporateMemberRole) => {
        if (!id) return;
        setInviting(true);
        setError(null);
        setFeedback(null);
        try {
            const res = await inviteCompanyMember(id, { email, role });
            // Prefer the backend-composed web link (M3.1); fall back to
            // rewriting the app://join deep link for older backends.
            const tokenMatch = /[?&]token=([^&]+)/.exec(res.invite_url ?? "");
            const webLink =
                res.web_invite_url ??
                (tokenMatch && typeof window !== "undefined"
                    ? `${window.location.origin}/company-login?invite_token=${tokenMatch[1]}`
                    : res.invite_url);
            if (res.email_sent) {
                setFeedback({ kind: "ok", text: `Invite emailed to ${email}.`, link: webLink });
            } else {
                // Delivery failed or backend predates M3.1 — never pretend it
                // was emailed; hand the admin the link to share manually.
                setFeedback({
                    kind: "warn",
                    text: `Invite created for ${email}, but the email could not be sent — share the link manually.`,
                    link: webLink,
                });
            }
            await load();
        } catch (e) {
            setError(e instanceof Error ? e.message : "Invite failed");
        } finally {
            setInviting(false);
        }
    };

    const onInvite = async () => {
        if (!inviteEmail.trim()) return;
        await sendInvite(inviteEmail.trim(), inviteRole);
        setInviteEmail("");
    };

    const copyLink = async (link: string) => {
        try {
            await navigator.clipboard.writeText(link);
            setFeedback((f) => (f ? { ...f, text: `${f.text.replace(/ \(link copied\)$/, "")} (link copied)` } : f));
        } catch {
            /* clipboard unavailable — the link is visible in the banner */
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
                        <div
                            className={
                                "flex flex-wrap items-center gap-2 rounded p-2 text-xs " +
                                (feedback.kind === "ok"
                                    ? "bg-emerald-50 dark:bg-emerald-900/20 text-emerald-800 dark:text-emerald-300"
                                    : "bg-amber-50 dark:bg-amber-900/10 text-amber-800 dark:text-amber-300")
                            }
                        >
                            <span>{feedback.text}</span>
                            {feedback.link && (
                                <button
                                    type="button"
                                    className="inline-flex items-center gap-1 font-medium underline"
                                    onClick={() => copyLink(feedback.link!)}
                                >
                                    <Copy className="h-3 w-3" /> Copy invite link
                                </button>
                            )}
                        </div>
                    )}
                    {error && (
                        <p className="rounded bg-destructive/10 p-2 text-xs text-destructive">{error}</p>
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
                                        {m.status === "invited" && m.invited_email && (
                                            <Button
                                                size="sm"
                                                variant="ghost"
                                                disabled={inviting}
                                                onClick={() => sendInvite(m.invited_email!, m.role)}
                                            >
                                                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                                                Resend
                                            </Button>
                                        )}
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
