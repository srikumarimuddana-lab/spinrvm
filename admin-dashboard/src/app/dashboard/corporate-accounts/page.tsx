"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import {
    listCorporateAccounts,
    createCorporateAccount,
    updateCorporateAccount,
    deleteCorporateAccount,
    getWalletRiskPortfolio,
    getKybReverificationDue,
    CorporateAccount,
    CompanyStatus,
    WalletRiskEntry,
    KybReverificationCompany,
} from "@/lib/api";
import { Pagination } from "@/components/ui/pagination";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
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
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import {
    AlertDialog,
    AlertDialogAction,
    AlertDialogCancel,
    AlertDialogContent,
    AlertDialogDescription,
    AlertDialogFooter,
    AlertDialogHeader,
    AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Building2, Plus, Pencil, Trash2, Search, Mail, Phone, RefreshCw, ShieldCheck, ShieldAlert, AlertTriangle } from "lucide-react";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useToast } from "@/components/ui/use-toast";

const STATUS_PILL_CLASSES: Record<CompanyStatus, string> = {
    pending_verification: "bg-yellow-100 text-yellow-800 hover:bg-yellow-100",
    active: "bg-emerald-100 text-emerald-800 hover:bg-emerald-100",
    suspended: "bg-orange-100 text-orange-800 hover:bg-orange-100",
    closed: "bg-gray-200 text-gray-700 hover:bg-gray-200",
};

const RISK_FLAG_LABELS: Record<string, string> = {
    negative_balance: "Negative balance",
    at_floor: "At/below soft floor",
    below_autotopup_threshold: "Below auto-topup threshold",
    low_balance_no_autotopup: "Low balance, auto-topup off",
};

function StatusPill({ status }: { status: CompanyStatus }) {
    return (
        <Badge variant="secondary" className={STATUS_PILL_CLASSES[status]}>
            {status.replace("_", " ")}
        </Badge>
    );
}

const PAGE_SIZE = 50;

export default function CorporateAccountsPage() {
    const { allowed } = useRequireModule("corporate_accounts");
    const { toast } = useToast();
    const [accounts, setAccounts] = useState<CorporateAccount[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");
    const [statusFilter, setStatusFilter] = useState<CompanyStatus | "all">("all");
    const [page, setPage] = useState(0);
    const [hasNextPage, setHasNextPage] = useState(false);
    const reqIdRef = useRef(0);

    // Corporate + admin portal review, round 2: "no portfolio-level view of
    // corporate wallet risk."
    const [flaggedWallets, setFlaggedWallets] = useState<WalletRiskEntry[]>([]);
    const [totalWallets, setTotalWallets] = useState(0);
    const [riskLoading, setRiskLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        getWalletRiskPortfolio()
            .then((res) => {
                if (cancelled) return;
                setFlaggedWallets(res.wallets.filter((w) => w.risk_flags.length > 0));
                setTotalWallets(res.total_wallets);
            })
            .catch(() => {
                if (!cancelled) {
                    setFlaggedWallets([]);
                    setTotalWallets(0);
                }
            })
            .finally(() => {
                if (!cancelled) setRiskLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    // Corporate + admin portal review, round 2: "automated KYB
    // re-verification" — visibility only, never auto-changes status.
    const [kybDue, setKybDue] = useState<KybReverificationCompany[]>([]);
    const [kybThresholdMonths, setKybThresholdMonths] = useState(12);
    const [kybLoading, setKybLoading] = useState(true);

    useEffect(() => {
        let cancelled = false;
        getKybReverificationDue()
            .then((res) => {
                if (cancelled) return;
                setKybDue(res.companies);
                setKybThresholdMonths(res.threshold_months);
            })
            .catch(() => {
                if (!cancelled) setKybDue([]);
            })
            .finally(() => {
                if (!cancelled) setKybLoading(false);
            });
        return () => {
            cancelled = true;
        };
    }, []);

    // Dialog states
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
    const [currentAccount, setCurrentAccount] = useState<any>(null);
    const [formLoading, setFormLoading] = useState(false);

    // Form state (M1.6: rich B2B fields land at create time; owner_email
    // seeds the company's first owner member — create-only, not a column)
    const emptyForm = {
        name: "",
        contact_name: "",
        contact_email: "",
        contact_phone: "",
        credit_limit: 0,
        is_active: true,
        legal_name: "",
        business_number: "",
        tax_region: "",
        size_tier: "",
        industry: "",
        owner_email: ""
    };
    const [formData, setFormData] = useState(emptyForm);

    // Reset page on filter changes; search is debounced below.
    useEffect(() => { setPage(0); }, [statusFilter]);

    useEffect(() => {
        const handle = setTimeout(() => { setPage(0); }, 300);
        return () => clearTimeout(handle);
    }, [search]);

    useEffect(() => { fetchAccounts(); }, [page, statusFilter, search]);

    const fetchAccounts = async () => {
        setLoading(true);
        const reqId = ++reqIdRef.current;
        try {
            const rows = await listCorporateAccounts({
                status: statusFilter === "all" ? undefined : statusFilter,
                search: search.trim() || undefined,
                skip: page * PAGE_SIZE,
                limit: PAGE_SIZE + 1,
            });
            if (reqId !== reqIdRef.current) return;
            const arr = Array.isArray(rows) ? rows : [];
            setHasNextPage(arr.length > PAGE_SIZE);
            setAccounts(arr.slice(0, PAGE_SIZE));
        } catch (error) {
            if (reqId !== reqIdRef.current) return;
            console.error("Failed to fetch corporate accounts:", error);
            toast({ title: "Failed to load accounts", variant: "destructive" });
            setAccounts([]);
            setHasNextPage(false);
        } finally {
            if (reqId === reqIdRef.current) setLoading(false);
        }
    };

    const handleOpenCreate = () => {
        setCurrentAccount(null);
        setFormData(emptyForm);
        setIsDialogOpen(true);
    };

    const handleOpenEdit = (account: CorporateAccount) => {
        setCurrentAccount(account);
        setFormData({
            name: account.name,
            contact_name: account.contact_name || "",
            contact_email: account.contact_email || "",
            contact_phone: account.contact_phone || "",
            credit_limit: account.credit_limit || 0,
            is_active: account.is_active,
            legal_name: (account as any).legal_name || "",
            business_number: (account as any).business_number || "",
            tax_region: (account as any).tax_region || "",
            size_tier: (account as any).size_tier || "",
            industry: (account as any).industry || "",
            owner_email: "" // create-only; never edited
        });
        setIsDialogOpen(true);
    };

    const handleOpenDelete = (account: CorporateAccount) => {
        setCurrentAccount(account);
        setIsDeleteDialogOpen(true);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setFormLoading(true);
        try {
            // Empty optional strings → omitted (backend validators reject "").
            const { owner_email, ...rest } = formData;
            const payload: Record<string, unknown> = { ...rest };
            for (const k of ["legal_name", "business_number", "tax_region", "size_tier", "industry"]) {
                if (!(payload[k] as string)?.trim()) delete payload[k];
            }
            if (currentAccount) {
                await updateCorporateAccount(currentAccount.id, payload);
            } else {
                if (owner_email.trim()) payload.owner_email = owner_email.trim();
                const created = (await createCorporateAccount(payload)) as CorporateAccount & {
                    owner_invite_url?: string | null;
                    owner_bootstrap_error?: boolean;
                };
                if (created.owner_bootstrap_error) {
                    toast({
                        title: "Company created, but the owner invite failed",
                        description: "Re-invite the owner from the company's Members page.",
                        variant: "destructive",
                    });
                } else if (created.owner_invite_url) {
                    const webLink = created.owner_invite_url.replace(
                        /^app:\/\/join\?token=/,
                        `${window.location.origin}/company-login?invite_token=`
                    );
                    try {
                        await navigator.clipboard.writeText(webLink);
                        toast({ title: "Owner invited", description: "Invite link copied to clipboard." });
                    } catch {
                        toast({ title: "Owner invited", description: webLink });
                    }
                }
            }
            setIsDialogOpen(false);
            fetchAccounts();
        } catch (error) {
            console.error("Failed to save account:", error);
            toast({ title: "Failed to save account", description: "Please try again.", variant: "destructive" });
        } finally {
            setFormLoading(false);
        }
    };

    const handleDelete = async () => {
        if (!currentAccount) return;
        setFormLoading(true);
        try {
            await deleteCorporateAccount(currentAccount.id);
            setIsDeleteDialogOpen(false);
            fetchAccounts();
        } catch (error) {
            console.error("Failed to delete account:", error);
            toast({ title: "Failed to delete account", variant: "destructive" });
        } finally {
            setFormLoading(false);
        }
    };

    const { sorted, sort, toggle } = useTableSort(accounts);

    if (!allowed) return null;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Corporate Accounts</h1>
                    <p className="text-muted-foreground mt-1">
                        Manage corporate clients and billing.
                    </p>
                </div>
                <div className="flex gap-2">
                    <Button asChild variant="outline">
                        <Link href="/dashboard/corporate-accounts/kyb-queue">
                            <ShieldCheck className="mr-2 h-4 w-4" /> KYB Queue
                        </Link>
                    </Button>
                    <Button variant="outline" size="icon" onClick={fetchAccounts} aria-label="Refresh">
                        <RefreshCw className="h-4 w-4" />
                    </Button>
                    <Button onClick={handleOpenCreate}>
                        <Plus className="mr-2 h-4 w-4" /> Add Account
                    </Button>
                </div>
            </div>

            {/* Wallet risk portfolio — flags across every company at once,
                so a risk doesn't require opening each account individually. */}
            {!riskLoading && flaggedWallets.length > 0 && (
                <Card className="border-amber-300/50 bg-amber-50/40 dark:bg-amber-950/10">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-amber-800 dark:text-amber-400 mb-2">
                            <AlertTriangle className="h-4 w-4" />
                            {flaggedWallets.length} of {totalWallets} wallets flagged
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {flaggedWallets.slice(0, 12).map((w) => (
                                <Link
                                    key={w.wallet_id}
                                    href={`/dashboard/corporate-accounts/${w.company_id}`}
                                    className="flex items-center gap-1.5 rounded-md border border-amber-300/60 bg-background px-2.5 py-1 text-xs hover:border-amber-500 transition"
                                    title={w.risk_flags.map((f) => RISK_FLAG_LABELS[f] || f).join(", ")}
                                >
                                    <span className="font-medium">{w.company_name || w.company_id.slice(0, 8)}</span>
                                    <span className="text-muted-foreground">${w.balance}</span>
                                </Link>
                            ))}
                            {flaggedWallets.length > 12 && (
                                <span className="text-xs text-muted-foreground self-center">
                                    +{flaggedWallets.length - 12} more
                                </span>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            {/* KYB re-verification staleness — corporate + admin portal
                review, round 2. Visibility only: this never changes a
                company's status, it's a reminder for an admin to manually
                re-run the KYB review flow. */}
            {!kybLoading && kybDue.length > 0 && (
                <Card className="border-sky-300/50 bg-sky-50/40 dark:bg-sky-950/10">
                    <CardContent className="p-4">
                        <div className="flex items-center gap-2 text-sm font-semibold text-sky-800 dark:text-sky-400 mb-2">
                            <ShieldAlert className="h-4 w-4" />
                            {kybDue.length} compan{kybDue.length === 1 ? "y" : "ies"} due for KYB re-verification
                            <span className="font-normal text-muted-foreground">
                                (approved &gt;{kybThresholdMonths} months ago)
                            </span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {kybDue.slice(0, 12).map((c) => (
                                <Link
                                    key={c.id}
                                    href={`/dashboard/corporate-accounts/${c.id}`}
                                    className="flex items-center gap-1.5 rounded-md border border-sky-300/60 bg-background px-2.5 py-1 text-xs hover:border-sky-500 transition"
                                    title={c.kyb_reviewed_at ? `Last reviewed ${c.kyb_reviewed_at.slice(0, 10)}` : ""}
                                >
                                    <span className="font-medium">{c.legal_name || c.name || c.id.slice(0, 8)}</span>
                                    {c.kyb_reviewed_at && (
                                        <span className="text-muted-foreground">
                                            {c.kyb_reviewed_at.slice(0, 10)}
                                        </span>
                                    )}
                                </Link>
                            ))}
                            {kybDue.length > 12 && (
                                <span className="text-xs text-muted-foreground self-center">
                                    +{kybDue.length - 12} more
                                </span>
                            )}
                        </div>
                    </CardContent>
                </Card>
            )}

            <div className="flex items-center gap-2">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search accounts..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <Select
                    value={statusFilter}
                    onValueChange={(v) => setStatusFilter(v as CompanyStatus | "all")}
                >
                    <SelectTrigger className="w-52" aria-label="Filter by status">
                        <SelectValue placeholder="Filter by status" />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value="all">All statuses</SelectItem>
                        <SelectItem value="pending_verification">Pending verification</SelectItem>
                        <SelectItem value="active">Active</SelectItem>
                        <SelectItem value="suspended">Suspended</SelectItem>
                        <SelectItem value="closed">Closed</SelectItem>
                    </SelectContent>
                </Select>
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="name" sort={sort} onSort={toggle}>Company Name</SortableHead>
                                <SortableHead column="contact_name" sort={sort} onSort={toggle}>Contact Person</SortableHead>
                                <SortableHead column="contact_email" sort={sort} onSort={toggle}>Contact Info</SortableHead>
                                <SortableHead column="credit_limit" sort={sort} onSort={toggle} align="right">Credit Limit</SortableHead>
                                <SortableHead column="is_active" sort={sort} onSort={toggle}>Status</SortableHead>
                                <TableHead className="text-right">Actions</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {loading ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="text-center py-10">
                                        <div className="flex justify-center">
                                            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                                        </div>
                                    </TableCell>
                                </TableRow>
                            ) : accounts.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                                        No corporate accounts found.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                sorted.map((account) => (
                                    <TableRow key={account.id}>
                                        <TableCell className="font-medium">
                                            <div className="flex items-center gap-2">
                                                <Building2 className="h-4 w-4 text-muted-foreground" />
                                                <Link
                                                    href={`/dashboard/corporate-accounts/${account.id}`}
                                                    className="hover:underline"
                                                >
                                                    {account.name}
                                                </Link>
                                                <StatusPill status={account.status} />
                                            </div>
                                        </TableCell>
                                        <TableCell>{account.contact_name || "N/A"}</TableCell>
                                        <TableCell>
                                            <div className="flex flex-col text-sm text-muted-foreground">
                                                {account.contact_email && (
                                                    <div className="flex items-center gap-1">
                                                        <Mail className="h-3 w-3" /> {account.contact_email}
                                                    </div>
                                                )}
                                                {account.contact_phone && (
                                                    <div className="flex items-center gap-1">
                                                        <Phone className="h-3 w-3" /> {account.contact_phone}
                                                    </div>
                                                )}
                                                {!account.contact_email && !account.contact_phone && "N/A"}
                                            </div>
                                        </TableCell>
                                        <TableCell>${account.credit_limit?.toLocaleString() || "0"}</TableCell>
                                        <TableCell>
                                            <Badge variant={account.is_active ? "default" : "secondary"} className={account.is_active ? "bg-emerald-500 hover:bg-emerald-600" : ""}>
                                                {account.is_active ? "Active" : "Inactive"}
                                            </Badge>
                                        </TableCell>
                                        <TableCell className="text-right">
                                            <div className="flex justify-end gap-2">
                                                <Button variant="ghost" size="icon" onClick={() => handleOpenEdit(account)}>
                                                    <Pencil className="h-4 w-4 text-muted-foreground" />
                                                </Button>
                                                <Button variant="ghost" size="icon" onClick={() => handleOpenDelete(account)}>
                                                    <Trash2 className="h-4 w-4 text-red-500" />
                                                </Button>
                                            </div>
                                        </TableCell>
                                    </TableRow>
                                ))
                            )}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            <Pagination
                page={page}
                pageSize={PAGE_SIZE}
                hasNextPage={hasNextPage}
                onPageChange={setPage}
            />

            {/* Create/Edit Dialog */}
            <Dialog open={isDialogOpen} onOpenChange={setIsDialogOpen}>
                <DialogContent>
                    <DialogHeader>
                        <DialogTitle>{currentAccount ? "Edit Account" : "Add Corporate Account"}</DialogTitle>
                        <DialogDescription>
                            {currentAccount ? "Update existing corporate account details." : "Create a new corporate account for business rides."}
                        </DialogDescription>
                    </DialogHeader>
                    <form onSubmit={handleSubmit} className="space-y-4">
                        <div className="space-y-2">
                            <Label htmlFor="name">Company Name</Label>
                            <Input
                                id="name"
                                value={formData.name}
                                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                                required
                                placeholder="e.g. Acme Corp"
                            />
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="contact_name">Contact Name</Label>
                                <Input
                                    id="contact_name"
                                    value={formData.contact_name}
                                    onChange={(e) => setFormData({ ...formData, contact_name: e.target.value })}
                                    placeholder="e.g. John Doe"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="credit_limit">Credit Limit ($)</Label>
                                <Input
                                    id="credit_limit"
                                    type="number"
                                    value={formData.credit_limit}
                                    onChange={(e) => setFormData({ ...formData, credit_limit: parseInt(e.target.value) || 0 })}
                                    min="0"
                                />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="contact_email">Email</Label>
                                <Input
                                    id="contact_email"
                                    type="email"
                                    value={formData.contact_email}
                                    onChange={(e) => setFormData({ ...formData, contact_email: e.target.value })}
                                    placeholder="admin@acme.com"
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="contact_phone">Phone</Label>
                                <Input
                                    id="contact_phone"
                                    value={formData.contact_phone}
                                    onChange={(e) => setFormData({ ...formData, contact_phone: e.target.value })}
                                    placeholder="+1 234 567 890"
                                />
                            </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="legal_name">Legal Name</Label>
                                <Input
                                    id="legal_name"
                                    value={formData.legal_name}
                                    onChange={(e) => setFormData({ ...formData, legal_name: e.target.value })}
                                    placeholder="Acme Corporation Inc."
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="business_number">CRA Business Number</Label>
                                <Input
                                    id="business_number"
                                    value={formData.business_number}
                                    onChange={(e) => setFormData({ ...formData, business_number: e.target.value })}
                                    placeholder="123456789RT0001"
                                />
                            </div>
                        </div>
                        <div className="grid grid-cols-3 gap-4">
                            <div className="space-y-2">
                                <Label htmlFor="tax_region">Tax Region</Label>
                                <Input
                                    id="tax_region"
                                    value={formData.tax_region}
                                    onChange={(e) => setFormData({ ...formData, tax_region: e.target.value })}
                                    placeholder="SK"
                                    maxLength={2}
                                />
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="size_tier">Size Tier</Label>
                                <select
                                    id="size_tier"
                                    className="h-9 w-full rounded-md border border-input bg-transparent px-3 text-sm"
                                    value={formData.size_tier}
                                    onChange={(e) => setFormData({ ...formData, size_tier: e.target.value })}
                                >
                                    <option value="">—</option>
                                    <option value="smb">SMB</option>
                                    <option value="mid_market">Mid-market</option>
                                    <option value="enterprise">Enterprise</option>
                                </select>
                            </div>
                            <div className="space-y-2">
                                <Label htmlFor="industry">Industry</Label>
                                <Input
                                    id="industry"
                                    value={formData.industry}
                                    onChange={(e) => setFormData({ ...formData, industry: e.target.value })}
                                    placeholder="Automotive"
                                />
                            </div>
                        </div>
                        {!currentAccount && (
                            <div className="space-y-2">
                                <Label htmlFor="owner_email">Owner Email (optional)</Label>
                                <Input
                                    id="owner_email"
                                    type="email"
                                    value={formData.owner_email}
                                    onChange={(e) => setFormData({ ...formData, owner_email: e.target.value })}
                                    placeholder="owner@acme.com"
                                />
                                <p className="text-xs text-muted-foreground">
                                    Invites this person as the company&apos;s first owner — they manage
                                    members and bookings from the business portal.
                                </p>
                            </div>
                        )}
                        <div className="flex items-center justify-between space-x-2 pt-2">
                            <Label htmlFor="is_active">Account Active Status</Label>
                            <Switch
                                id="is_active"
                                checked={formData.is_active}
                                onCheckedChange={(checked) => setFormData({ ...formData, is_active: checked })}
                            />
                        </div>
                        <DialogFooter className="pt-4">
                            <Button type="button" variant="outline" onClick={() => setIsDialogOpen(false)}>
                                Cancel
                            </Button>
                            <Button type="submit" disabled={formLoading}>
                                {formLoading ? "Saving..." : "Save Account"}
                            </Button>
                        </DialogFooter>
                    </form>
                </DialogContent>
            </Dialog>

            {/* Delete Confirmation */}
            <AlertDialog open={isDeleteDialogOpen} onOpenChange={setIsDeleteDialogOpen}>
                <AlertDialogContent>
                    <AlertDialogHeader>
                        <AlertDialogTitle>Are you sure?</AlertDialogTitle>
                        <AlertDialogDescription>
                            This will permanently delete the corporate account &quot;{currentAccount?.name}&quot;.
                            This action cannot be undone.
                        </AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction onClick={handleDelete} className="bg-red-600 hover:bg-red-700">
                            {formLoading ? "Deleting..." : "Delete"}
                        </AlertDialogAction>
                    </AlertDialogFooter>
                </AlertDialogContent>
            </AlertDialog>
        </div>
    );
}
