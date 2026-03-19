"use client";

import { useEffect, useState } from "react";
import { Ticket, Plus, Trash2, ToggleLeft, ToggleRight, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PromoCode {
    id: string;
    code: string;
    discount_type: "flat" | "percentage";
    discount_value: number;
    max_discount?: number;
    max_uses: number;
    max_uses_per_user: number;
    uses: number;
    expiry_date?: string;
    is_active: boolean;
    description?: string;
    created_at: string;
}

export default function PromotionsPage() {
    const [promos, setPromos] = useState<PromoCode[]>([]);
    const [loading, setLoading] = useState(true);
    const [showCreate, setShowCreate] = useState(false);

    // Create form state
    const [code, setCode] = useState("");
    const [discountType, setDiscountType] = useState<"flat" | "percentage">("flat");
    const [discountValue, setDiscountValue] = useState("");
    const [maxDiscount, setMaxDiscount] = useState("");
    const [maxUses, setMaxUses] = useState("100");
    const [maxUsesPerUser, setMaxUsesPerUser] = useState("1");
    const [expiryDate, setExpiryDate] = useState("");
    const [description, setDescription] = useState("");
    const [creating, setCreating] = useState(false);

    const token = typeof window !== "undefined" ? localStorage.getItem("admin_token") : "";
    const headers = { "Content-Type": "application/json", Authorization: `Bearer ${token}` };

    const fetchPromos = async () => {
        try {
            const res = await fetch(`${API_BASE}/api/admin/promotions`, { headers });
            const data = await res.json();
            setPromos(Array.isArray(data) ? data : []);
        } catch {
            console.error("Failed to fetch promos");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => { fetchPromos(); }, []);

    const handleCreate = async () => {
        if (!code.trim() || !discountValue) return;
        setCreating(true);
        try {
            await fetch(`${API_BASE}/api/admin/promotions`, {
                method: "POST", headers,
                body: JSON.stringify({
                    code: code.trim().toUpperCase(),
                    discount_type: discountType,
                    discount_value: parseFloat(discountValue),
                    max_discount: maxDiscount ? parseFloat(maxDiscount) : null,
                    max_uses: parseInt(maxUses),
                    max_uses_per_user: parseInt(maxUsesPerUser),
                    expiry_date: expiryDate || null,
                    description: description || null,
                }),
            });
            setShowCreate(false);
            setCode(""); setDiscountValue(""); setDescription("");
            await fetchPromos();
        } catch { console.error("Create failed"); }
        finally { setCreating(false); }
    };

    const toggleActive = async (promo: PromoCode) => {
        await fetch(`${API_BASE}/api/admin/promotions/${promo.id}`, {
            method: "PUT", headers,
            body: JSON.stringify({ is_active: !promo.is_active }),
        });
        await fetchPromos();
    };

    const deletePromo = async (id: string) => {
        if (!confirm("Delete this promo code?")) return;
        await fetch(`${API_BASE}/api/admin/promotions/${id}`, { method: "DELETE", headers });
        await fetchPromos();
    };

    return (
        <div>
            <div className="flex items-center justify-between mb-8">
                <div>
                    <h1 className="text-2xl font-bold flex items-center gap-2">
                        <Ticket className="h-6 w-6 text-violet-600" /> Promotions
                    </h1>
                    <p className="text-muted-foreground mt-1">Manage promo codes and discounts</p>
                </div>
                <Button onClick={() => setShowCreate(!showCreate)} className="gap-2">
                    <Plus className="h-4 w-4" /> Create Promo Code
                </Button>
            </div>

            {/* Create Form */}
            {showCreate && (
                <div className="border rounded-xl p-6 mb-6 bg-card">
                    <h2 className="font-semibold text-lg mb-4">New Promo Code</h2>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Code</label>
                            <input className="w-full mt-1 px-3 py-2 border rounded-lg text-sm uppercase tracking-widest font-mono" value={code} onChange={e => setCode(e.target.value)} placeholder="e.g. SAVE10" />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Type</label>
                            <select className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={discountType} onChange={e => setDiscountType(e.target.value as any)}>
                                <option value="flat">Flat ($)</option>
                                <option value="percentage">Percentage (%)</option>
                            </select>
                        </div>
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Value</label>
                            <input type="number" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={discountValue} onChange={e => setDiscountValue(e.target.value)} placeholder={discountType === "flat" ? "5.00" : "10"} />
                        </div>
                        {discountType === "percentage" && (
                            <div>
                                <label className="text-sm font-medium text-muted-foreground">Max Discount Cap ($)</label>
                                <input type="number" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={maxDiscount} onChange={e => setMaxDiscount(e.target.value)} placeholder="25.00" />
                            </div>
                        )}
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Max Uses</label>
                            <input type="number" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={maxUses} onChange={e => setMaxUses(e.target.value)} />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Per User Limit</label>
                            <input type="number" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={maxUsesPerUser} onChange={e => setMaxUsesPerUser(e.target.value)} />
                        </div>
                        <div>
                            <label className="text-sm font-medium text-muted-foreground">Expiry Date</label>
                            <input type="date" className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={expiryDate} onChange={e => setExpiryDate(e.target.value)} />
                        </div>
                        <div className="md:col-span-2">
                            <label className="text-sm font-medium text-muted-foreground">Description</label>
                            <input className="w-full mt-1 px-3 py-2 border rounded-lg text-sm" value={description} onChange={e => setDescription(e.target.value)} placeholder="Optional description" />
                        </div>
                    </div>
                    <div className="flex gap-3 mt-4">
                        <Button onClick={handleCreate} disabled={creating}>{creating ? "Creating..." : "Create"}</Button>
                        <Button variant="ghost" onClick={() => setShowCreate(false)}>Cancel</Button>
                    </div>
                </div>
            )}

            {/* Table */}
            {loading ? (
                <div className="text-center py-12 text-muted-foreground">Loading...</div>
            ) : promos.length === 0 ? (
                <div className="text-center py-12 text-muted-foreground">No promo codes yet. Create one to get started.</div>
            ) : (
                <div className="border rounded-xl overflow-hidden">
                    <table className="w-full text-sm">
                        <thead className="bg-muted/50">
                            <tr>
                                <th className="text-left px-4 py-3 font-medium">Code</th>
                                <th className="text-left px-4 py-3 font-medium">Discount</th>
                                <th className="text-center px-4 py-3 font-medium">Uses</th>
                                <th className="text-left px-4 py-3 font-medium">Expiry</th>
                                <th className="text-center px-4 py-3 font-medium">Status</th>
                                <th className="text-right px-4 py-3 font-medium">Actions</th>
                            </tr>
                        </thead>
                        <tbody>
                            {promos.map(p => (
                                <tr key={p.id} className="border-t hover:bg-muted/30 transition-colors">
                                    <td className="px-4 py-3">
                                        <span className="font-mono font-semibold text-violet-600 bg-violet-50 px-2 py-0.5 rounded">{p.code}</span>
                                        {p.description && <p className="text-xs text-muted-foreground mt-0.5">{p.description}</p>}
                                    </td>
                                    <td className="px-4 py-3">
                                        {p.discount_type === "flat" ? `$${p.discount_value.toFixed(2)}` : `${p.discount_value}%`}
                                        {p.max_discount && <span className="text-xs text-muted-foreground ml-1">(max ${p.max_discount})</span>}
                                    </td>
                                    <td className="text-center px-4 py-3">{p.uses}/{p.max_uses}</td>
                                    <td className="px-4 py-3 text-muted-foreground">{p.expiry_date ? new Date(p.expiry_date).toLocaleDateString() : "—"}</td>
                                    <td className="text-center px-4 py-3">
                                        <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${p.is_active ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700"}`}>
                                            {p.is_active ? "Active" : "Inactive"}
                                        </span>
                                    </td>
                                    <td className="px-4 py-3 text-right">
                                        <div className="flex gap-1 justify-end">
                                            <button onClick={() => toggleActive(p)} className="p-1.5 hover:bg-muted rounded" title={p.is_active ? "Deactivate" : "Activate"}>
                                                {p.is_active ? <ToggleRight className="h-4 w-4 text-green-600" /> : <ToggleLeft className="h-4 w-4 text-muted-foreground" />}
                                            </button>
                                            <button onClick={() => deletePromo(p.id)} className="p-1.5 hover:bg-red-50 rounded text-red-500" title="Delete">
                                                <Trash2 className="h-4 w-4" />
                                            </button>
                                        </div>
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}
        </div>
    );
}
