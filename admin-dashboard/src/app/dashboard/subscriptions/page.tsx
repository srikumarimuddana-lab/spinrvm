"use client";

import { useEffect, useState } from "react";
import { getSubscriptionPlans, createSubscriptionPlan, updateSubscriptionPlan, deleteSubscriptionPlan, getDriverSubscriptions } from "@/lib/api";
import { CreditCard, Plus, Edit, Trash2, Users, Infinity, ToggleLeft, ToggleRight } from "lucide-react";

interface Plan {
  id: string;
  name: string;
  price: number;
  duration_days: number;
  rides_per_day: number;
  description: string;
  features: string[];
  vehicle_types: string[] | null;
  service_areas: string[] | null;
  is_active: boolean;
  subscriber_count: number;
}

const DURATION_OPTIONS = [
  { label: "Daily", value: 1 },
  { label: "Weekly", value: 7 },
  { label: "Monthly", value: 30 },
  { label: "Yearly", value: 365 },
];

export default function SubscriptionsPage() {
  const [plans, setPlans] = useState<Plan[]>([]);
  const [subs, setSubs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<"plans" | "subscribers">("plans");

  // Form
  const [form, setForm] = useState({
    name: "", price: "", duration_days: 30, rides_per_day: -1,
    description: "", features: "" as string, service_areas: "" as string,
    is_active: true,
  });

  useEffect(() => { load(); }, []);

  const load = async () => {
    setLoading(true);
    try {
      const [p, s] = await Promise.all([getSubscriptionPlans(), getDriverSubscriptions()]);
      setPlans(p); setSubs(s);
    } catch {}
    setLoading(false);
  };

  const handleSubmit = async () => {
    if (!form.name || !form.price) return;
    const data = {
      name: form.name,
      price: parseFloat(form.price),
      duration_days: form.duration_days,
      rides_per_day: form.rides_per_day,
      description: form.description,
      features: form.features ? form.features.split(",").map(f => f.trim()).filter(Boolean) : [],
      service_areas: form.service_areas ? form.service_areas.split(",").map(a => a.trim()).filter(Boolean) : null,
      is_active: form.is_active,
    };
    try {
      if (editingId) {
        await updateSubscriptionPlan(editingId, data);
      } else {
        await createSubscriptionPlan(data);
      }
      resetForm(); load();
    } catch (e: any) {
      alert(e?.message || "Failed to save plan");
    }
  };

  const handleEdit = (p: Plan) => {
    setEditingId(p.id);
    setForm({
      name: p.name, price: String(p.price), duration_days: p.duration_days,
      rides_per_day: p.rides_per_day, description: p.description || "",
      features: (p.features || []).join(", "),
      service_areas: (p.service_areas || []).join(", "),
      is_active: p.is_active,
    });
    setShowForm(true);
  };

  const handleToggle = async (p: Plan) => {
    await updateSubscriptionPlan(p.id, { is_active: !p.is_active });
    load();
  };

  const handleDelete = async (p: Plan) => {
    if (!confirm(`Delete "${p.name}" plan? Active subscribers will keep their plan until expiry.`)) return;
    await deleteSubscriptionPlan(p.id); load();
  };

  const resetForm = () => {
    setShowForm(false); setEditingId(null);
    setForm({ name: "", price: "", duration_days: 30, rides_per_day: -1, description: "", features: "", service_areas: "", is_active: true });
  };

  const getDurationLabel = (days: number) => DURATION_OPTIONS.find(d => d.value === days)?.label || `${days} days`;

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">Spinr Pass</h1>
          <p className="text-gray-500 mt-1">Manage driver subscription plans</p>
        </div>
        <button onClick={() => { resetForm(); setShowForm(true); }} className="flex items-center gap-2 bg-red-500 text-white px-5 py-2.5 rounded-xl font-semibold hover:bg-red-600 transition">
          <Plus className="h-5 w-5" /> New Plan
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 mb-6 w-fit">
        <button onClick={() => setActiveTab("plans")} className={`px-5 py-2 rounded-lg text-sm font-semibold transition ${activeTab === "plans" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500"}`}>
          Plans ({plans.length})
        </button>
        <button onClick={() => setActiveTab("subscribers")} className={`px-5 py-2 rounded-lg text-sm font-semibold transition ${activeTab === "subscribers" ? "bg-white text-gray-900 shadow-sm" : "text-gray-500"}`}>
          Subscribers ({subs.length})
        </button>
      </div>

      {/* Add/Edit Form */}
      {showForm && (
        <div className="bg-white rounded-2xl border p-6 mb-6 shadow-sm">
          <h3 className="text-lg font-bold mb-4">{editingId ? "Edit Plan" : "New Subscription Plan"}</h3>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Plan Name *</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" placeholder="e.g. Basic, Pro, Unlimited" value={form.name} onChange={e => setForm({...form, name: e.target.value})} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Price (CAD) *</label>
              <div className="relative">
                <span className="absolute left-4 top-1/2 -translate-y-1/2 text-gray-400 font-semibold">$</span>
                <input className="w-full border rounded-xl pl-8 pr-4 py-2.5 text-sm" type="number" step="0.01" placeholder="19.99" value={form.price} onChange={e => setForm({...form, price: e.target.value})} />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Duration</label>
              <select className="w-full border rounded-xl px-4 py-2.5 text-sm" value={form.duration_days} onChange={e => setForm({...form, duration_days: parseInt(e.target.value)})}>
                {DURATION_OPTIONS.map(d => <option key={d.value} value={d.value}>{d.label}</option>)}
              </select>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Rides Per Day</label>
              <div className="flex gap-2">
                <button onClick={() => setForm({...form, rides_per_day: -1})} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${form.rides_per_day === -1 ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>
                  Unlimited
                </button>
                {[4, 8, 12, 20].map(n => (
                  <button key={n} onClick={() => setForm({...form, rides_per_day: n})} className={`px-4 py-2 rounded-xl text-sm font-semibold border ${form.rides_per_day === n ? "bg-red-500 text-white border-red-500" : "bg-white border-gray-200"}`}>
                    {n}
                  </button>
                ))}
                <input className="w-20 border rounded-xl px-3 py-2 text-sm text-center" type="number" min="1" placeholder="#" value={form.rides_per_day > 0 && ![4,8,12,20].includes(form.rides_per_day) ? form.rides_per_day : ""} onChange={e => setForm({...form, rides_per_day: parseInt(e.target.value) || 1})} />
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Description</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" placeholder="Brief description of the plan" value={form.description} onChange={e => setForm({...form, description: e.target.value})} />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Features (comma-separated)</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" placeholder="Priority support, Surge protection, Earnings boost" value={form.features} onChange={e => setForm({...form, features: e.target.value})} />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-600 mb-1">Service Areas (IDs, comma-separated — leave blank for all)</label>
              <input className="w-full border rounded-xl px-4 py-2.5 text-sm" placeholder="Leave blank for all areas" value={form.service_areas} onChange={e => setForm({...form, service_areas: e.target.value})} />
            </div>
          </div>

          <div className="flex items-center gap-3 mb-6">
            <label className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={form.is_active} onChange={e => setForm({...form, is_active: e.target.checked})} className="accent-red-500 w-4 h-4" />
              <span className="text-sm font-medium text-gray-700">Active (visible to drivers)</span>
            </label>
          </div>

          <div className="flex gap-3">
            <button onClick={handleSubmit} className="bg-red-500 text-white px-6 py-2.5 rounded-xl font-semibold hover:bg-red-600">
              {editingId ? "Save Changes" : "Create Plan"}
            </button>
            <button onClick={resetForm} className="bg-gray-100 text-gray-600 px-6 py-2.5 rounded-xl font-semibold hover:bg-gray-200">
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Plans Tab */}
      {activeTab === "plans" && (
        loading ? (
          <div className="text-center py-12 text-gray-400">Loading plans...</div>
        ) : plans.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl border">
            <CreditCard className="h-12 w-12 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-bold text-gray-700">No subscription plans yet</h3>
            <p className="text-gray-400 mt-1">Create your first Spinr Pass plan for drivers</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {plans.map(p => (
              <div key={p.id} className={`bg-white rounded-2xl border p-6 relative ${!p.is_active ? "opacity-50" : ""}`}>
                {/* Status toggle */}
                <button onClick={() => handleToggle(p)} className="absolute top-4 right-4">
                  {p.is_active ? <ToggleRight className="h-6 w-6 text-green-500" /> : <ToggleLeft className="h-6 w-6 text-gray-300" />}
                </button>

                <div className="mb-4">
                  <h3 className="text-xl font-bold text-gray-900">{p.name}</h3>
                  <p className="text-gray-500 text-sm mt-1">{p.description}</p>
                </div>

                <div className="mb-4">
                  <span className="text-3xl font-extrabold text-red-500">${p.price.toFixed(2)}</span>
                  <span className="text-gray-400 text-sm ml-1">/ {getDurationLabel(p.duration_days).toLowerCase()}</span>
                </div>

                <div className="space-y-2 mb-4">
                  <div className="flex items-center gap-2 text-sm">
                    {p.rides_per_day === -1 ? (
                      <><Infinity className="h-4 w-4 text-green-500" /><span className="text-gray-700">Unlimited rides/day</span></>
                    ) : (
                      <><span className="font-bold text-red-500">{p.rides_per_day}</span><span className="text-gray-700">rides per day</span></>
                    )}
                  </div>
                  <div className="flex items-center gap-2 text-sm">
                    <Users className="h-4 w-4 text-gray-400" />
                    <span className="text-gray-500">{p.subscriber_count || 0} subscribers</span>
                  </div>
                  {p.service_areas && p.service_areas.length > 0 && (
                    <div className="text-xs text-orange-600 bg-orange-50 px-2 py-1 rounded-md w-fit">
                      Area restricted ({p.service_areas.length} area{p.service_areas.length > 1 ? "s" : ""})
                    </div>
                  )}
                </div>

                {(p.features || []).length > 0 && (
                  <div className="border-t pt-3 mb-4">
                    {p.features.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 text-sm text-gray-600 py-0.5">
                        <span className="text-green-500">✓</span> {f}
                      </div>
                    ))}
                  </div>
                )}

                <div className="flex gap-2 pt-2 border-t">
                  <button onClick={() => handleEdit(p)} className="flex-1 flex items-center justify-center gap-1 py-2 text-sm font-semibold text-gray-600 hover:bg-gray-50 rounded-lg transition">
                    <Edit className="h-4 w-4" /> Edit
                  </button>
                  <button onClick={() => handleDelete(p)} className="flex-1 flex items-center justify-center gap-1 py-2 text-sm font-semibold text-red-500 hover:bg-red-50 rounded-lg transition">
                    <Trash2 className="h-4 w-4" /> Delete
                  </button>
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* Subscribers Tab */}
      {activeTab === "subscribers" && (
        subs.length === 0 ? (
          <div className="text-center py-16 bg-white rounded-2xl border">
            <Users className="h-12 w-12 text-gray-300 mx-auto mb-4" />
            <h3 className="text-lg font-bold text-gray-700">No subscribers yet</h3>
            <p className="text-gray-400 mt-1">Drivers will appear here when they subscribe to a plan</p>
          </div>
        ) : (
          <div className="bg-white rounded-2xl border overflow-hidden">
            <table className="w-full text-sm">
              <thead className="bg-gray-50 text-left">
                <tr>
                  <th className="px-5 py-3 font-semibold text-gray-600">Driver</th>
                  <th className="px-5 py-3 font-semibold text-gray-600">Plan</th>
                  <th className="px-5 py-3 font-semibold text-gray-600">Price</th>
                  <th className="px-5 py-3 font-semibold text-gray-600">Rides/Day</th>
                  <th className="px-5 py-3 font-semibold text-gray-600">Status</th>
                  <th className="px-5 py-3 font-semibold text-gray-600">Expires</th>
                </tr>
              </thead>
              <tbody>
                {subs.map(s => (
                  <tr key={s.id} className="border-t">
                    <td className="px-5 py-3 font-medium">{s.driver_id?.slice(0, 8)}...</td>
                    <td className="px-5 py-3">{s.plan_name}</td>
                    <td className="px-5 py-3 font-semibold">${s.price?.toFixed(2)}</td>
                    <td className="px-5 py-3">{s.rides_per_day === -1 ? "∞" : s.rides_per_day}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${
                        s.status === 'active' ? 'bg-green-100 text-green-700' :
                        s.status === 'expired' ? 'bg-yellow-100 text-yellow-700' :
                        'bg-gray-100 text-gray-600'
                      }`}>{s.status?.toUpperCase()}</span>
                    </td>
                    <td className="px-5 py-3 text-gray-500">{s.expires_at ? new Date(s.expires_at).toLocaleDateString() : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </div>
  );
}
