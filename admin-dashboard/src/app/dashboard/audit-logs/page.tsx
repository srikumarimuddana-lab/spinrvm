"use client";

import { useEffect, useState } from "react";
import { getAuditLogs } from "@/lib/api";
import { Shield, User, Car, MapPin, CreditCard, Settings, Ticket, RefreshCw } from "lucide-react";

const ENTITY_ICONS: Record<string, any> = {
  driver: Car, user: User, ride: Car, promotion: Ticket,
  service_area: MapPin, staff: User, setting: Settings,
  subscription: CreditCard,
};

const ACTION_COLORS: Record<string, string> = {
  created: "bg-green-100 text-green-700",
  updated: "bg-blue-100 text-blue-700",
  deleted: "bg-red-100 text-red-700",
  login: "bg-purple-100 text-purple-700",
  status_change: "bg-yellow-100 text-yellow-700",
};

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try { setLogs(await getAuditLogs(100)); } catch {}
    setLoading(false);
  };

  useEffect(() => { load(); }, []);

  const formatTime = (d: string) => {
    try { return new Date(d).toLocaleString('en-CA', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }); }
    catch { return d; }
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Audit Logs</h1>
          <p className="text-gray-500 mt-1">Track all admin actions and changes</p>
        </div>
        <button onClick={load} className="flex items-center gap-2 bg-gray-100 dark:bg-gray-800 px-4 py-2 rounded-xl text-sm font-semibold hover:bg-gray-200 transition">
          <RefreshCw className="h-4 w-4" /> Refresh
        </button>
      </div>

      {loading ? (
        <div className="text-center py-12 text-gray-400">Loading logs...</div>
      ) : logs.length === 0 ? (
        <div className="text-center py-16 bg-white dark:bg-gray-900 rounded-2xl border">
          <Shield className="h-12 w-12 text-gray-300 mx-auto mb-4" />
          <h3 className="text-lg font-bold text-gray-700 dark:text-gray-300">No audit logs yet</h3>
          <p className="text-gray-400 mt-1">Admin actions will be recorded here</p>
        </div>
      ) : (
        <div className="bg-white dark:bg-gray-900 rounded-2xl border overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 dark:bg-gray-800 text-left">
              <tr>
                <th className="px-5 py-3 font-semibold text-gray-600 dark:text-gray-400">Time</th>
                <th className="px-5 py-3 font-semibold text-gray-600 dark:text-gray-400">User</th>
                <th className="px-5 py-3 font-semibold text-gray-600 dark:text-gray-400">Action</th>
                <th className="px-5 py-3 font-semibold text-gray-600 dark:text-gray-400">Entity</th>
                <th className="px-5 py-3 font-semibold text-gray-600 dark:text-gray-400">Details</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => {
                const Icon = ENTITY_ICONS[log.entity_type] || Shield;
                return (
                  <tr key={log.id} className="border-t dark:border-gray-800">
                    <td className="px-5 py-3 text-gray-500 whitespace-nowrap">{formatTime(log.created_at)}</td>
                    <td className="px-5 py-3 font-medium dark:text-gray-300">{log.user_email}</td>
                    <td className="px-5 py-3">
                      <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${ACTION_COLORS[log.action] || 'bg-gray-100 text-gray-600'}`}>
                        {log.action?.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2">
                        <Icon className="h-4 w-4 text-gray-400" />
                        <span className="dark:text-gray-300">{log.entity_type}</span>
                        <span className="text-gray-400 text-xs">{log.entity_id?.slice(0, 8)}...</span>
                      </div>
                    </td>
                    <td className="px-5 py-3 text-gray-500 max-w-xs truncate">{log.details}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
