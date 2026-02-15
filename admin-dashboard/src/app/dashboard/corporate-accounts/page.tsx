"use client";

import { useEffect, useState } from "react";
import {
    getCorporateAccounts,
    createCorporateAccount,
    updateCorporateAccount,
    deleteCorporateAccount
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
    DialogTrigger,
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
import { Building2, Plus, Pencil, Trash2, Search, Mail, Phone, RefreshCw } from "lucide-react";

export default function CorporateAccountsPage() {
    const [accounts, setAccounts] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState("");

    // Dialog states
    const [isDialogOpen, setIsDialogOpen] = useState(false);
    const [isDeleteDialogOpen, setIsDeleteDialogOpen] = useState(false);
    const [currentAccount, setCurrentAccount] = useState<any>(null);
    const [formLoading, setFormLoading] = useState(false);

    // Form state
    const [formData, setFormData] = useState({
        name: "",
        contact_name: "",
        contact_email: "",
        contact_phone: "",
        credit_limit: 0,
        is_active: true
    });

    useEffect(() => {
        fetchAccounts();
    }, []);

    const fetchAccounts = async () => {
        setLoading(true);
        try {
            const data = await getCorporateAccounts();
            setAccounts(data);
        } catch (error) {
            console.error("Failed to fetch corporate accounts:", error);
        } finally {
            setLoading(false);
        }
    };

    const handleOpenCreate = () => {
        setCurrentAccount(null);
        setFormData({
            name: "",
            contact_name: "",
            contact_email: "",
            contact_phone: "",
            credit_limit: 0,
            is_active: true
        });
        setIsDialogOpen(true);
    };

    const handleOpenEdit = (account: any) => {
        setCurrentAccount(account);
        setFormData({
            name: account.name,
            contact_name: account.contact_name || "",
            contact_email: account.contact_email || "",
            contact_phone: account.contact_phone || "",
            credit_limit: account.credit_limit || 0,
            is_active: account.is_active
        });
        setIsDialogOpen(true);
    };

    const handleOpenDelete = (account: any) => {
        setCurrentAccount(account);
        setIsDeleteDialogOpen(true);
    };

    const handleSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        setFormLoading(true);
        try {
            if (currentAccount) {
                await updateCorporateAccount(currentAccount.id, formData);
            } else {
                await createCorporateAccount(formData);
            }
            setIsDialogOpen(false);
            fetchAccounts();
        } catch (error) {
            console.error("Failed to save account:", error);
            alert("Failed to save account. Please try again.");
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
            alert("Failed to delete account.");
        } finally {
            setFormLoading(false);
        }
    };

    const filteredAccounts = accounts.filter(acc =>
        acc.name.toLowerCase().includes(search.toLowerCase()) ||
        acc.contact_name?.toLowerCase().includes(search.toLowerCase()) ||
        acc.contact_email?.toLowerCase().includes(search.toLowerCase())
    );

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
                    <Button variant="outline" size="icon" onClick={fetchAccounts}>
                        <RefreshCw className="h-4 w-4" />
                    </Button>
                    <Button onClick={handleOpenCreate}>
                        <Plus className="mr-2 h-4 w-4" /> Add Account
                    </Button>
                </div>
            </div>

            <div className="flex items-center space-x-2">
                <div className="relative flex-1 max-w-sm">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                        placeholder="Search accounts..."
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                        className="pl-9"
                    />
                </div>
            </div>

            <Card className="border-border/50">
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Company Name</TableHead>
                                <TableHead>Contact Person</TableHead>
                                <TableHead>Contact Info</TableHead>
                                <TableHead>Credit Limit</TableHead>
                                <TableHead>Status</TableHead>
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
                            ) : filteredAccounts.length === 0 ? (
                                <TableRow>
                                    <TableCell colSpan={6} className="text-center py-10 text-muted-foreground">
                                        No corporate accounts found.
                                    </TableCell>
                                </TableRow>
                            ) : (
                                filteredAccounts.map((account) => (
                                    <TableRow key={account.id}>
                                        <TableCell className="font-medium">
                                            <div className="flex items-center gap-2">
                                                <Building2 className="h-4 w-4 text-muted-foreground" />
                                                {account.name}
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
                            This will permanently delete the corporate account "{currentAccount?.name}".
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
