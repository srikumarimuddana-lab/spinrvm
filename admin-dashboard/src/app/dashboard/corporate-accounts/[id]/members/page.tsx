"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
    ArrowLeft,
    Check,
    Mail,
    PauseCircle,
    PlayCircle,
    Trash2,
    Wallet,
    X,
} from "lucide-react";

import {
    AllowanceRequestRow,
    CorporateMember,
    CorporateMemberStatus,
    decideAllowanceRequest,
    inviteCompanyMember,
    listCompanyAllowanceRequests,
    listCompanyMembers,
    removeCompanyMember,
    updateCompanyMember,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
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
import {
    Dialog,
    DialogContent,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import AllowanceDialog from "./allowance-dialog";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";

const STATUS_COLORS: Record<CorporateMember["status"], string> = {
    invited: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    suspended: "bg-orange-100 text-orange-800 dark:bg-orange-900/40 dark:text-orange-300",
    removed: "bg-gray-100 text-gray-600 dark:bg-gray-800 dark:text-gray-400",
};

const REQUEST_STATUS_COLORS: Record<AllowanceRequestRow["status"], string> = {
    pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-300",
    approved: "bg-emerald-100 text-emerald-800 dark:bg-emerald-900/40 dark:text-emerald-300",
    auto_approved: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
    denied: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300",
};

type StatusFilter = "all" | CorporateMemberStatus;
type RequestStatusFilter = "pending" | "approved" | "denied" | "all";

const MEMBER_STATUS_TABS: { label: string; value: StatusFilter }[] = [
    { label: "All", value: "all" },
    { label: "Active", value: "active" },
    { label: "Invited", value: "invited" },
    { label: "Suspended", value: "suspended" },
];

const REQUEST_STATUS_TABS: { label: string; value: RequestStatusFilter }[] = [
    { label: "Pending", value: "pending" },
    { label: "Approved", value: "approved" },
    { label: "Denied", value: "denied" },
    { label: "All", value: "all" },
];

function formatDate(iso?: string | null) {
    if (!iso) return "—";
    return new Date(iso).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });
}

interface DecisionDialogProps {
    request: AllowanceRequestRow;
    approve: boolean;
    onConfirm: (note: string) => void;
    onCancel: () => void;
    busy: boolean;
}

function DecisionDialog({ request, approve, onConfirm, onCancel, busy }: DecisionDialogProps) {
    const [note, setNote] = useState("");
    return (
        <Dialog open onOpenChange={(o) => (!o ? onCancel() : undefined)}>
            <DialogContent className="max-w-md">
                <DialogHeader>
                    <DialogTitle>
                        {approve ? "Approve" : "Deny"} request — ${Number(request.amount).toFixed(2)}
                    </DialogTitle>
                </DialogHeader>
                <div className="space-y-3 py-2">
                    <p className="text-sm text-muted-foreground">
                        <b>Reason submitted:</b> {request.reason}
                    </p>
                    <div>
                        <Label htmlFor="decision-note">
                            {approve ? "Note (optional)" : "Denial reason (optional)"}
                        </Label>
                        <Textarea
                            id="decision-note"
                            value={note}
                            onChange={(e) => setNote(e.target.value)}
                            rows={3}
                            maxLength={500}
                            placeholder={approve ? "e.g. Approved for Q2 travel" : "e.g. Budget exceeded for this period"}
                        />
                    </div>
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={onCancel} disabled={busy}>Cancel</Button>
                    <Button
                        onClick={() => onConfirm(note)}
                        disabled={busy}
                        className={approve ? "bg-emerald-600 hover:bg-emerald-700" : "bg-red-600 hover:bg-red-700"}
                    >
                        {busy ? "Working…" : approve ? "Approve" : "Deny"}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

export default function CompanyMembersPage() {
    const { id } = useParams<{ id: string }>();
    const companyId = id as string;
    const { toast } = useToast();
    const [removeTarget, setRemoveTarget] = useState<string | null>(null);

    const [members, setMembers] = useState<CorporateMember[]>([]);
    const [requests, setRequests] = useState<AllowanceRequestRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState<CorporateMember["role"]>("member");
    const [inviteUrl, setInviteUrl] = useState<string | null>(null);
    const [inviting, setInviting] = useState(false);
    const [editTarget, setEditTarget] = useState<CorporateMember | null>(null);

    const [memberFilter, setMemberFilter] = useState<StatusFilter>("all");
    const [requestFilter, setRequestFilter] = useState<RequestStatusFilter>("pending");

    const [decisionTarget, setDecisionTarget] = useState<{ req: AllowanceRequestRow; approve: boolean } | null>(null);
    const [decisionBusy, setDecisionBusy] = useState(false);

    const refresh = useCallback(async () => {
        setLoading(true);
        try {
            const [ms, rs] = await Promise.all([
                listCompanyMembers(companyId),
                listCompanyAllowanceRequests(companyId, "all"),
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
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(inviteEmail.trim())) {
            toast({ title: "Invalid email address", description: "Please enter a valid email", variant: "destructive" });
            return;
        }
        setInviting(true);
        try {
            const res = await inviteCompanyMember(companyId, { email: inviteEmail, role: inviteRole });
            setInviteEmail("");
            setInviteUrl(res.invite_url);
            refresh();
        } catch (e: any) {
            toast({ title: "Invite failed", description: e?.message, variant: "destructive" });
        } finally {
            setInviting(false);
        }
    }

    async function handleRemove(memberId: string) {
        setRemoveTarget(memberId);
    }

    async function confirmRemove() {
        if (!removeTarget) return;
        try {
            await removeCompanyMember(companyId, removeTarget);
            refresh();
        } catch (e: any) {
            toast({ title: "Remove failed", description: e?.message, variant: "destructive" });
        } finally {
            setRemoveTarget(null);
        }
    }

    async function handleStatusChange(member: CorporateMember, newStatus: "suspended" | "active") {
        try {
            await updateCompanyMember(companyId, member.id, { status: newStatus });
            refresh();
        } catch (e: any) {
            toast({ title: "Status change failed", description: e?.message, variant: "destructive" });
        }
    }

    async function handleDecisionConfirm(note: string) {
        if (!decisionTarget) return;
        setDecisionBusy(true);
        try {
            await decideAllowanceRequest(companyId, decisionTarget.req.id, {
                approve: decisionTarget.approve,
                note: note.trim() || undefined,
            });
            setDecisionTarget(null);
            refresh();
        } catch (e: any) {
            toast({ title: "Decision failed", description: e?.message, variant: "destructive" });
        } finally {
            setDecisionBusy(false);
        }
    }

    const filteredMembers = members.filter(
        (m) => memberFilter === "all" || m.status === memberFilter
    );

    const filteredRequests = requests.filter(
        (r) => requestFilter === "all" || r.status === requestFilter
    );

    const { sorted: sortedMembers, sort: memberSort, toggle: toggleMemberSort } =
        useTableSort(filteredMembers);
    const { sorted: sortedRequests, sort: requestSort, toggle: toggleRequestSort } =
        useTableSort(filteredRequests);

    const pendingCount = requests.filter((r) => r.status === "pending").length;

    return (
        <div className="space-y-6 p-6">
            {/* Header */}
            <div className="flex items-center gap-3">
                <Link href={`/dashboard/corporate-accounts/${companyId}`}>
                    <Button variant="ghost" size="sm">
                        <ArrowLeft className="mr-1 h-4 w-4" />
                        Company
                    </Button>
                </Link>
                <h1 className="text-2xl font-semibold">Members</h1>
                <Link href={`/dashboard/corporate-accounts/${companyId}/policy`}>
                    <Button variant="outline" size="sm">Policy editor →</Button>
                </Link>
            </div>

            {/* Invite form */}
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
                            <Select
                                value={inviteRole}
                                onValueChange={(v) => setInviteRole(v as CorporateMember["role"])}
                            >
                                <SelectTrigger className="w-36">
                                    <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                    <SelectItem value="member">Member</SelectItem>
                                    <SelectItem value="admin">Admin</SelectItem>
                                    <SelectItem value="owner">Owner</SelectItem>
                                </SelectContent>
                            </Select>
                        </div>
                        <Button type="submit" disabled={inviting}>
                            <Mail className="mr-1 h-4 w-4" />
                            {inviting ? "Sending…" : "Send invite"}
                        </Button>
                    </form>
                    {inviteUrl && (
                        <div className="flex items-start gap-2 rounded border border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/40 p-3 text-sm">
                            <Check className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                            <div>
                                <p className="font-medium text-emerald-700 dark:text-emerald-300">Invite created</p>
                                <code className="mt-1 block break-all text-xs text-emerald-800 dark:text-emerald-200">
                                    {inviteUrl}
                                </code>
                            </div>
                        </div>
                    )}
                </CardContent>
            </Card>

            {/* Members table */}
            <Card>
                <CardContent className="p-0">
                    {/* Status filter tabs */}
                    <div className="flex gap-1 border-b px-4 pt-3">
                        {MEMBER_STATUS_TABS.map((tab) => (
                            <button
                                key={tab.value}
                                onClick={() => setMemberFilter(tab.value)}
                                className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
                                    memberFilter === tab.value
                                        ? "border-primary text-foreground"
                                        : "border-transparent text-muted-foreground hover:text-foreground"
                                }`}
                            >
                                {tab.label}
                                {tab.value !== "all" && (
                                    <span className="ml-1.5 text-xs text-muted-foreground">
                                        ({members.filter((m) => m.status === tab.value).length})
                                    </span>
                                )}
                            </button>
                        ))}
                    </div>

                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="invited_email" sort={memberSort} onSort={toggleMemberSort}>Email / ID</SortableHead>
                                <SortableHead column="role" sort={memberSort} onSort={toggleMemberSort}>Role</SortableHead>
                                <SortableHead column="status" sort={memberSort} onSort={toggleMemberSort}>Status</SortableHead>
                                <SortableHead column="created_at" sort={memberSort} onSort={toggleMemberSort}>Joined</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                                        Loading…
                                    </TableCell>
                                </TableRow>
                            ) : filteredMembers.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={5} className="text-center text-muted-foreground py-8">
                                        No members in this view.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                sortedMembers.map((m) => (
                                    <TableRow key={m.id}>
                                        <TableCell className="font-mono text-xs">
                                            {m.invited_email ?? m.user_id ?? m.id}
                                        </TableCell>
                                        <TableCell className="capitalize">{m.role}</TableCell>
                                        <TableCell>
                                            <Badge className={STATUS_COLORS[m.status]}>
                                                {m.status}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="text-muted-foreground text-sm">
                                            {formatDate(m.created_at)}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex items-center justify-end gap-1">
                                                <Button
                                                    size="sm"
                                                    variant="outline"
                                                    onClick={() => setEditTarget(m)}
                                                >
                                                    <Wallet className="mr-1 h-3.5 w-3.5" />
                                                    Allowance
                                                </Button>
                                                {m.status === "active" && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        className="text-orange-700 dark:text-orange-400 hover:text-orange-800 dark:hover:text-orange-300"
                                                        onClick={() => handleStatusChange(m, "suspended")}
                                                        title="Suspend member"
                                                    >
                                                        <PauseCircle className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                                {m.status === "suspended" && (
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        className="text-emerald-700 dark:text-emerald-400 hover:text-emerald-800 dark:hover:text-emerald-300"
                                                        onClick={() => handleStatusChange(m, "active")}
                                                        title="Reactivate member"
                                                    >
                                                        <PlayCircle className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                                {m.status !== "removed" && (
                                                    <Button
                                                        size="sm"
                                                        variant="ghost"
                                                        className="text-destructive hover:text-destructive/80"
                                                        onClick={() => handleRemove(m.id)}
                                                        title="Remove member"
                                                    >
                                                        <Trash2 className="h-3.5 w-3.5" />
                                                    </Button>
                                                )}
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Allowance requests */}
            <Card>
                <CardContent className="p-0">
                    <div className="flex items-center justify-between px-4 pt-3 pb-2">
                        <h2 className="font-semibold">
                            Allowance requests
                            {pendingCount > 0 && (
                                <span className="ml-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-yellow-500 text-xs font-bold text-white">
                                    {pendingCount}
                                </span>
                            )}
                        </h2>
                        <div className="flex gap-1">
                            {REQUEST_STATUS_TABS.map((tab) => (
                                <button
                                    key={tab.value}
                                    onClick={() => setRequestFilter(tab.value)}
                                    className={`rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                                        requestFilter === tab.value
                                            ? "bg-primary text-primary-foreground"
                                            : "bg-muted text-muted-foreground hover:bg-muted/80"
                                    }`}
                                >
                                    {tab.label}
                                </button>
                            ))}
                        </div>
                    </div>

                    {filteredRequests.length === 0 ? (
                        <p className="px-4 pb-4 text-sm text-muted-foreground">
                            No {requestFilter !== "all" ? requestFilter : ""} requests.
                        </p>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <SortableHead column="member_id" sort={requestSort} onSort={toggleRequestSort}>Member</SortableHead>
                                    <SortableHead column="amount" sort={requestSort} onSort={toggleRequestSort}>Amount</SortableHead>
                                    <SortableHead column="reason" sort={requestSort} onSort={toggleRequestSort}>Reason</SortableHead>
                                    <SortableHead column="status" sort={requestSort} onSort={toggleRequestSort}>Status</SortableHead>
                                    <SortableHead column="created_at" sort={requestSort} onSort={toggleRequestSort}>Submitted</SortableHead>
                                    <TableHead className="text-right">Actions</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {sortedRequests.map((r) => (
                                    <TableRow key={r.id}>
                                        <TableCell className="font-mono text-xs text-muted-foreground max-w-[14ch] truncate">
                                            {r.member_id}
                                        </TableCell>
                                        <TableCell className="font-semibold tabular-nums">
                                            ${Number(r.amount).toFixed(2)}
                                        </TableCell>
                                        <TableCell className="max-w-[28ch] truncate text-sm">
                                            {r.reason}
                                        </TableCell>
                                        <TableCell>
                                            <Badge className={REQUEST_STATUS_COLORS[r.status]}>
                                                {r.status.replace("_", " ")}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="text-muted-foreground text-sm">
                                            {formatDate(r.created_at)}
                                        </TableCell>
                                        <TableCell className="text-right">
                                            {r.status === "pending" && (
                                                <div className="flex items-center justify-end gap-1">
                                                    <Button
                                                        size="sm"
                                                        className="bg-emerald-600 hover:bg-emerald-700 h-7"
                                                        onClick={() => setDecisionTarget({ req: r, approve: true })}
                                                    >
                                                        <Check className="mr-1 h-3 w-3" /> Approve
                                                    </Button>
                                                    <Button
                                                        size="sm"
                                                        variant="outline"
                                                        className="text-destructive h-7"
                                                        onClick={() => setDecisionTarget({ req: r, approve: false })}
                                                    >
                                                        <X className="mr-1 h-3 w-3" /> Deny
                                                    </Button>
                                                </div>
                                            )}
                                            {r.status !== "pending" && r.decision_notes && (
                                                <span className="text-xs text-muted-foreground max-w-[20ch] truncate block text-right" title={r.decision_notes}>
                                                    {r.decision_notes}
                                                </span>
                                            )}
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
                    onSaved={() => { setEditTarget(null); refresh(); }}
                />
            )}

            {decisionTarget && (
                <DecisionDialog
                    request={decisionTarget.req}
                    approve={decisionTarget.approve}
                    onConfirm={handleDecisionConfirm}
                    onCancel={() => setDecisionTarget(null)}
                    busy={decisionBusy}
                />
            )}

            <AlertDialog open={!!removeTarget} onOpenChange={(open) => { if (!open) setRemoveTarget(null); }}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Remove member?</AlertDialogTitle>
                        <AlertDialogDescription>Their allowance stops immediately. This cannot be undone.</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={confirmRemove} className="bg-red-600 hover:bg-red-700">Remove</AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
