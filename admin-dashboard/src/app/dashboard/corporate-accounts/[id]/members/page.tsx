"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    ArrowLeft,
    Mail,
    Trash2,
    Wallet,
} from "lucide-react";

import {
    AllowanceRequestRow,
    CorporateAllowance,
    CorporateMember,
    decideAllowanceRequest,
    inviteCompanyMember,
    listCompanyAllowanceRequests,
    listCompanyMembers,
    removeCompanyMember,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

import AllowanceDialog from "./allowance-dialog";

const STATUS_COLORS: Record<CorporateMember["status"], string> = {
    invited: "bg-yellow-100 text-yellow-800",
    active: "bg-emerald-100 text-emerald-800",
    suspended: "bg-orange-100 text-orange-800",
    removed: "bg-gray-200 text-gray-700",
};

export default function CompanyMembersPage() {
    const { id } = useParams<{ id: string }>();
    const companyId = id as string;
    const [members, setMembers] = useState<CorporateMember[]>([]);
    const [requests, setRequests] = useState<AllowanceRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<CorporateMember["role"]>("member");
    const [inviteUrl, setInviteUrl] = useState<string | null>(null);
    const [editTarget, setEditTarget] = useState<CorporateMember | null>(null);

    const refresh = useCallback(async () => {
        setLoading(true);
        try {
            const [ms, rs] = await Promise.all([
                listCompanyMembers(companyId),
                listCompanyAllowanceRequests(companyId, "pending"),
            ]);
            setMembers(ms);
            setRequests(rs);
        } catch (err) {
            console.error("failed to load members/requests", err);
        } finally {
            setLoading(false);
        }
    }, [companyId]);

    useEffect(() => {
        if (companyId) refresh();
    }, [companyId, refresh]);

    async function handleInvite(e: React.FormEvent) {
        e.preventDefault();
        if (!inviteEmail) return;
        const res = await inviteCompanyMember(companyId, {
            email: inviteEmail,
            role: inviteRole,
        });
        setInviteEmail("");
        setInviteUrl(res.invite_url);
        refresh();
    }

    async function handleRemove(memberId: string) {
        if (!window.confirm("Remove this member? Their allowance stops immediately.")) {
            return;
        }
        await removeCompanyMember(companyId, memberId);
        refresh();
    }

    async function handleDecision(requestId: string, approve: boolean) {
        const note = window.prompt(approve ? "Approval note (optional)" : "Denial reason");
        if (!approve && note === null) return;
        await decideAllowanceRequest(companyId, requestId, {
            approve,
            note: note ?? undefined,
        });
        refresh();
    }

    return (
        <div className="space-y-6 p-6">
            <div className="flex items-center gap-3">
                <Link href={`/dashboard/corporate-accounts/${companyId}`}>
                    <Button variant="ghost" size="sm">
                        <ArrowLeft className="mr-1 h-4 w-4" />
                        Company
                    </Button>
                </Link>
                <h1 className="text-2xl font-semibold">Members</h1>
            </div>

            <Card>
                <CardContent className="space-y-4 p-4">
                    <form onSubmit={handleInvite} className="flex flex-wrap items-end gap-2">
                        <div>
                            <Label htmlFor="invite-email">Invite by email</Label>
                            <Input
                                id="invite-email"
                                type="email"
                                value={inviteEmail}
                                onChange={(e) => setInviteEmail(e.target.value)}
                                placeholder="employee@acme.com"
                                className="w-72"
                            />
                        </div>
                        <div>
                            <Label>Role</Label>
                            <Select value={inviteRole} onValueChange={(v) => setInviteRole(v as CorporateMember["role"])}>
                                <SelectTrigger className="w-36">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">member</SelectItem>
                                    <SelectItem value="admin">admin</SelectItem>
                                    <SelectItem value="owner">owner</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <Button type="submit">
                            <Mail className="mr-1 h-4 w-4" />
                            Send invite
                        </Button>
                    </form>
                    {inviteUrl && (
                        <div className="rounded border border-emerald-200 bg-emerald-50 p-2 text-sm">
                            Invite link: <code>{inviteUrl}</code>
                        </div>
                    )}
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Email</TableHead>
                                <TableHead>Role</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow><TableCell colSpan={4}>Loading…</TableCell></TableRow>
                            ) : members.length === 0 ? (
                                <TableRow><TableCell colSpan={4}>No members yet.</TableCell></TableRow>
                            ) : (
                                members.map((m) => (
                                    <TableRow key={m.id}>
                                        <TableCell>{m.invited_email ?? "—"}</TableCell>
                                        <TableCell>{m.role}</TableCell>
                                        <TableCell>
                                            <Badge className={STATUS_COLORS[m.status]}>{m.status}</Badge>
                                        </TableCell>
                                        <TableCell className="text-right space-x-2">
                                            <Button size="sm" variant="outline" onClick={() => setEditTarget(m)}>
                                                <Wallet className="mr-1 h-4 w-4" />
                                                Allowance
                                            </Button>
                                            <Button
                                                size="sm"
                                                variant="destructive"
                                                onClick={() => handleRemove(m.id)}
                                            >
                                                <Trash2 className="mr-1 h-4 w-4" />
                                                Remove
                                            </Button>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Card>
                <CardContent className="p-4">
                    <h2 className="mb-2 text-lg font-medium">Pending allowance requests</h2>
                    {requests.length === 0 ? (
                        <p className="text-sm text-gray-500">No pending requests.</p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Member</TableHead>
                                    <TableHead>Amount</TableHead>
                                    <TableHead>Reason</TableHead>
                                    <TableHead className="text-right">Decide</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {requests.map((r) => (
                                    <TableRow key={r.id}>
                                        <TableCell>{r.member_id}</TableCell>
                                        <TableCell>${r.amount.toFixed(2)}</TableCell>
                                        <TableCell className="max-w-[24ch] truncate">{r.reason}</TableCell>
                                        <TableCell className="text-right space-x-2">
                                            <Button size="sm" onClick={() => handleDecision(r.id, true)}>Approve</Button>
                                            <Button size="sm" variant="destructive" onClick={() => handleDecision(r.id, false)}>Deny</Button>
                                        </TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>

            {editTarget && (
                <AllowanceDialog
                    companyId={companyId}
                    member={editTarget}
                    onClose={() => setEditTarget(null)}
                    onSaved={() => {
                        setEditTarget(null);
                        refresh();
                    }}
                />
            )}
        </div>
    );
}
