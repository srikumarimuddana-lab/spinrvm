"use client";

import { useEffect, useState } from "react";
import { getStaff, createStaff, updateStaff, deleteStaff, resetStaffMfa } from "@/lib/api";
import { useAuthStore } from "@/store/authStore";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Users, Plus, Shield, ShieldCheck, ShieldOff, Eye, EyeOff, Trash2, Edit, Check, X } from "lucide-react";
import { useToast } from "@/components/ui/use-toast";
import {
    AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
    AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
// Corporate + admin portal review, round 2: "staff management is the one
// list page that breaks the portal's own pattern" — every other admin
// list page (audit-logs, safety, drivers, users, ...) renders through
// Table/SortableHead/Card/Button/Input, not raw <div>/<button>/<input>.
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";

// Must stay in step with AVAILABLE_MODULES in backend/routes/admin/staff.py.
// The create/update handlers filter submitted modules against that list, so a
// key offered here but absent there is a checkbox that ticks, saves, returns
// 200 — and grants nothing. Three had drifted into exactly that state and were
// removed on 2026-08-14:
//
//   heatmap           gated no backend route at all (only the sidebar link)
//   bulk_operations   deliberately removed backend-side; the Data Transfer
//                     surface is now require_super_admin, so offering it here
//                     implied a full-fidelity PII export could be delegated
//   compliance        the OPPOSITE drift — require_module("compliance") is a
//                     real, enforced gate (routes/admin/__init__.py) that is
//                     NOT in AVAILABLE_MODULES, so nobody can hold it and the
//                     router is super_admin-only by omission. Removed from the
//                     picker because it cannot currently be granted; making it
//                     grantable is a product decision about who may reach tax
//                     and compliance reporting, not a cleanup. Tracked as a
//                     follow-up rather than decided here.
//
// A test in backend/tests/test_admin_module_list_parity.py now compares the two
// lists so this cannot drift again silently.
const ALL_MODULES = [
  { key: "dashboard", label: "Dashboard" },
  { key: "users", label: "Users" },
  { key: "drivers", label: "Drivers" },
  { key: "rides", label: "Rides" },
  { key: "earnings", label: "Earnings" },
  { key: "promotions", label: "Promotions" },
  { key: "surge", label: "Surge Pricing" },
  { key: "service_areas", label: "Service Areas" },
  { key: "vehicle_types", label: "Vehicle Types" },
  { key: "pricing", label: "Pricing" },
  { key: "support", label: "Support" },
  { key: "disputes", label: "Disputes" },
  { key: "notifications", label: "Cloud Messaging" },
  { key: "settings", label: "Settings" },
  { key: "corporate_accounts", label: "Corporate Accounts" },
  { key: "documents", label: "Documents" },
  { key: "staff", label: "Staff Management" },
  { key: "audit", label: "Audit Logs" },
  { key: "support_tickets", label: "Help Desk (Zoho)" },
];

const ROLE_PRESETS: Record<string, string[]> = {
  super_admin: ALL_MODULES.map((m) => m.key),
  operations: ["dashboard", "rides", "drivers", "surge", "service_areas", "vehicle_types"],
  support: ["dashboard", "support", "support_tickets", "disputes", "notifications", "users"],
  finance: ["dashboard", "earnings", "promotions", "corporate_accounts", "pricing", "audit"],
};

const ROLE_COLORS: Record<string, string> = {
  super_admin: "bg-red-100 text-red-700",
  operations: "bg-blue-100 text-blue-700",
  support: "bg-green-100 text-green-700",
  finance: "bg-purple-100 text-purple-700",
  custom: "bg-gray-100 text-gray-700",
};

interface Staff {
  id: string;
  email: string;
  first_name: string;
  last_name: string;
  role: string;
  modules: string[];
  is_active: boolean;
  created_at: string;
  last_login?: string;
  mfa_enabled?: boolean;
}

export default function StaffPage() {
  const { allowed } = useRequireModule("staff");
  const { user } = useAuthStore();
  const { toast } = useToast();
  const [deleteTarget, setDeleteTarget] = useState<Staff | null>(null);
  const [mfaResetTarget, setMfaResetTarget] = useState<Staff | null>(null);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const { sorted, sort, toggle } = useTableSort(staff);

  // Form state
  const [form, setForm] = useState({
    email: "", password: "", first_name: "", last_name: "",
    role: "custom", modules: ["dashboard"] as string[],
  });

  useEffect(() => {
    loadStaff();
  }, []);

  const loadStaff = async () => {
    setLoading(true);
    try {
      const data = await getStaff();
      setStaff(Array.isArray(data) ? data : []);
    } catch (e: any) {
      // Every other admin list page surfaces a load failure via toast, not
      // a silent console.error only the developer console would show.
      toast({ title: "Failed to load staff", description: e?.message, variant: "destructive" });
    }
    setLoading(false);
  };

  const handleRoleChange = (role: string) => {
    setForm((f) => ({
      ...f,
      role,
      modules: role === "custom" ? f.modules : ROLE_PRESETS[role] || ["dashboard"],
    }));
  };

  const toggleModule = (mod: string) => {
    setForm((f) => ({
      ...f,
      modules: f.modules.includes(mod)
        ? f.modules.filter((m) => m !== mod)
        : [...f.modules, mod],
    }));
  };

  const handleSubmit = async () => {
    if (!form.email || !form.first_name || !form.last_name) return;
    try {
      if (editingId) {
        await updateStaff(editingId, {
          first_name: form.first_name,
          last_name: form.last_name,
          role: form.role,
          modules: form.modules,
        });
      } else {
        if (!form.password) return;
        await createStaff(form);
      }
      toast({ title: editingId ? "Staff member updated" : "Staff member created" });
      resetForm();
      loadStaff();
    } catch (e: any) {
      toast({ title: "Failed to save staff member", description: e?.message, variant: "destructive" });
    }
  };

  const handleEdit = (s: Staff) => {
    setEditingId(s.id);
    setForm({
      email: s.email,
      password: "",
      first_name: s.first_name,
      last_name: s.last_name,
      role: s.role,
      modules: s.modules || ["dashboard"],
    });
    setShowForm(true);
  };

  const handleToggleActive = async (s: Staff) => {
    try {
      await updateStaff(s.id, { is_active: !s.is_active });
      toast({ title: `Staff member ${!s.is_active ? 'activated' : 'deactivated'}` });
      loadStaff();
    } catch (e: any) {
      toast({ title: "Failed to update staff", description: e?.message, variant: "destructive" });
    }
  };

  const handleDelete = (s: Staff) => {
    setDeleteTarget(s);
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    try {
      await deleteStaff(deleteTarget.id);
      toast({ title: "Staff member deleted", description: `${deleteTarget.email} removed.` });
      loadStaff();
    } catch (e: any) {
      toast({ title: "Failed to delete staff member", description: e?.message, variant: "destructive" });
    } finally {
      setDeleteTarget(null);
    }
  };

  const confirmMfaReset = async () => {
    if (!mfaResetTarget) return;
    try {
      await resetStaffMfa(mfaResetTarget.id);
      toast({
        title: "MFA reset",
        description: `${mfaResetTarget.email} was signed out everywhere and can now log in with email + password, then re-enroll from Settings.`,
      });
      loadStaff();
    } catch (e: any) {
      toast({ title: "Failed to reset MFA", description: e?.message, variant: "destructive" });
    } finally {
      setMfaResetTarget(null);
    }
  };

  const resetForm = () => {
    setShowForm(false);
    setEditingId(null);
    setForm({ email: "", password: "", first_name: "", last_name: "", role: "custom", modules: ["dashboard"] });
  };

  // useRequireModule("staff") handles redirect; return null to avoid content flash.
  if (!allowed) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <Shield className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
          <h2 className="text-xl font-bold text-foreground">Access Denied</h2>
          <p className="text-muted-foreground mt-2">Only super admins can manage staff.</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Staff Management</h1>
          <p className="text-muted-foreground mt-1">Create staff accounts and control module access</p>
        </div>
        <Button onClick={() => { resetForm(); setShowForm(true); }}>
          <Plus className="h-4 w-4 mr-1.5" />
          Add Staff
        </Button>
      </div>

      {/* Add/Edit Form */}
      {showForm && (
        <Card className="border-border/50 mb-6">
          <CardContent className="p-6">
          <h3 className="text-lg font-bold mb-4">{editingId ? "Edit Staff" : "New Staff Member"}</h3>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <Label className="mb-1 block">First Name</Label>
              <Input
                value={form.first_name}
                onChange={(e) => setForm({ ...form, first_name: e.target.value })}
              />
            </div>
            <div>
              <Label className="mb-1 block">Last Name</Label>
              <Input
                value={form.last_name}
                onChange={(e) => setForm({ ...form, last_name: e.target.value })}
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <Label className="mb-1 block">Email</Label>
              <Input
                type="email"
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                disabled={!!editingId}
              />
            </div>
            {!editingId && (
              <div>
                <Label className="mb-1 block">Password</Label>
                <div className="relative">
                  <Input
                    className="pr-10"
                    type={showPassword ? "text" : "password"}
                    value={form.password}
                    onChange={(e) => setForm({ ...form, password: e.target.value })}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Role selector */}
          <label className="block text-sm font-medium text-muted-foreground mb-2">Role</label>
          <div className="flex flex-wrap gap-2 mb-4">
            {Object.keys(ROLE_PRESETS).map((role) => (
              <button
                key={role}
                onClick={() => handleRoleChange(role)}
                className={`px-4 py-2 rounded-xl text-sm font-semibold border transition ${
                  form.role === role
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-card text-muted-foreground border-border hover:border-muted-foreground"
                }`}
              >
                {role.replace("_", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </button>
            ))}
            <button
              onClick={() => handleRoleChange("custom")}
              className={`px-4 py-2 rounded-xl text-sm font-semibold border transition ${
                form.role === "custom"
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-card text-muted-foreground border-border hover:border-muted-foreground"
              }`}
            >
              Custom
            </button>
          </div>

          {/* Module checkboxes (show when custom or to show what preset includes) */}
          <label className="block text-sm font-medium text-muted-foreground mb-2">
            Module Access {form.role !== "custom" && <span className="text-muted-foreground">(preset — switch to Custom to edit)</span>}
          </label>
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-2 mb-6">
            {ALL_MODULES.map((mod) => (
              <label
                key={mod.key}
                className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-sm cursor-pointer transition ${
                  form.modules.includes(mod.key)
                    ? "bg-primary/10 border-primary/30 text-foreground"
                    : "bg-muted border-border text-foreground"
                } ${form.role !== "custom" ? "opacity-60 pointer-events-none" : ""}`}
              >
                <input
                  type="checkbox"
                  checked={form.modules.includes(mod.key)}
                  onChange={() => toggleModule(mod.key)}
                  disabled={form.role !== "custom"}
                  className="accent-primary"
                />
                {mod.label}
              </label>
            ))}
          </div>

          <div className="flex gap-3">
            <Button onClick={handleSubmit}>
              {editingId ? "Save Changes" : "Create Staff"}
            </Button>
            <Button variant="outline" onClick={resetForm}>
              Cancel
            </Button>
          </div>
          </CardContent>
        </Card>
      )}

      {/* Staff List — every other admin list page (audit-logs, safety,
          drivers, users) renders through Table/SortableHead, not a
          hand-rolled card list. Corporate + admin portal review, round 2. */}
      <Card className="border-border/50">
        <CardContent className="p-0">
          {loading ? (
            <div className="flex items-center justify-center p-12">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            </div>
          ) : staff.length === 0 ? (
            <div className="text-center py-16">
              <Users className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
              <h3 className="text-lg font-semibold">No staff members yet</h3>
              <p className="text-muted-foreground mt-1">Add your first team member to share admin access</p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <SortableHead column="first_name" sort={sort} onSort={toggle}>Name</SortableHead>
                  <SortableHead column="email" sort={sort} onSort={toggle}>Email</SortableHead>
                  <SortableHead column="role" sort={sort} onSort={toggle}>Role</SortableHead>
                  <TableHead>Modules</TableHead>
                  <TableHead>Status</TableHead>
                  <SortableHead column="last_login" sort={sort} onSort={toggle}>Last Login</SortableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sorted.map((s) => (
                  <TableRow key={s.id} className={!s.is_active ? "opacity-50" : ""}>
                    <TableCell>
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-full bg-muted flex items-center justify-center text-foreground font-bold text-xs shrink-0">
                          {s.first_name?.[0]}{s.last_name?.[0]}
                        </div>
                        <span className="font-medium">{s.first_name} {s.last_name}</span>
                      </div>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">{s.email}</TableCell>
                    <TableCell>
                      <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${ROLE_COLORS[s.role] || ROLE_COLORS.custom}`}>
                        {s.role.replace("_", " ").toUpperCase()}
                      </span>
                    </TableCell>
                    <TableCell>
                      <div className="flex flex-wrap gap-1 max-w-xs">
                        {(s.modules || []).map((m) => (
                          <span key={m} className="px-2 py-0.5 bg-muted text-foreground text-xs rounded-md">
                            {ALL_MODULES.find((am) => am.key === m)?.label || m}
                          </span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1.5">
                        {!s.is_active && (
                          <span className="px-2 py-0.5 rounded-md text-xs font-bold bg-yellow-100 text-yellow-700">DISABLED</span>
                        )}
                        {s.mfa_enabled && (
                          <span className="flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-bold bg-green-100 text-green-700">
                            <ShieldCheck className="h-3 w-3" />
                            MFA
                          </span>
                        )}
                      </div>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {s.last_login ? new Date(s.last_login).toLocaleString() : "—"}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        {/* Lost-phone recovery — super_admin only, never on your
                            own account (self-service goes through Settings /
                            backup codes; backend enforces both rules too). */}
                        {user?.role === "super_admin" && s.mfa_enabled && s.id !== user?.id && (
                          <button
                            onClick={() => setMfaResetTarget(s)}
                            className="p-2 hover:bg-orange-50 rounded-lg transition"
                            title="Reset MFA (lost phone)"
                          >
                            <ShieldOff className="h-4 w-4 text-orange-500" />
                          </button>
                        )}
                        <button onClick={() => handleEdit(s)} className="p-2 hover:bg-muted rounded-lg transition" title="Edit">
                          <Edit className="h-4 w-4 text-muted-foreground" />
                        </button>
                        <button
                          onClick={() => handleToggleActive(s)}
                          className="p-2 hover:bg-muted rounded-lg transition"
                          title={s.is_active ? "Disable" : "Enable"}
                        >
                          {s.is_active ? <X className="h-4 w-4 text-yellow-500" /> : <Check className="h-4 w-4 text-green-500" />}
                        </button>
                        <button onClick={() => handleDelete(s)} className="p-2 hover:bg-red-50 rounded-lg transition" title="Delete">
                          <Trash2 className="h-4 w-4 text-red-400" />
                        </button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={!!mfaResetTarget} onOpenChange={(open) => { if (!open) setMfaResetTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Reset MFA for {mfaResetTarget?.first_name} {mfaResetTarget?.last_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              Use this when they lost their phone or authenticator. Their two-factor
              setup and backup codes are wiped and they are signed out everywhere.
              They can then log in with email + password and re-enroll a new
              authenticator from Settings. The action is recorded in the audit log.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmMfaReset} className="bg-orange-600 hover:bg-orange-700">Reset MFA</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => { if (!open) setDeleteTarget(null); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {deleteTarget?.first_name} {deleteTarget?.last_name}?</AlertDialogTitle>
            <AlertDialogDescription>This cannot be undone.</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={confirmDelete} className="bg-red-600 hover:bg-red-700">Delete</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
