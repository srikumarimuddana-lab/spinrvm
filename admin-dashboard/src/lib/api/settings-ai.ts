// App settings and the super-admin AI console (catalog, chat, per-user
// conversation history). Extracted from the monolithic lib/api.ts as part
// of the per-domain split.

import { request } from "./client";

/* ── Settings ─────────────────────────────── */
export const getEmailDeliverability = (days = 7) =>
    request<{
        window_days: number;
        total: number;
        by_status: Record<string, number>;
        by_provider: Record<string, number>;
        by_type: Record<string, number>;
        failure_rate: number;
        suppressed_in_window: number;
        suppression_list_size: number;
        recent_failures: Array<{ email_type: string; provider: string; status: string; recipient_user_id: string | null; created_at: string }>;
        recent_suppressions: Array<{ reason: string; detail: string | null; source: string; message_id: string | null; created_at: string }>;
    }>(`/api/admin/monitoring/email-deliverability?days=${days}`);

export const getSettings = () => request<any>("/api/admin/settings");
export const updateSettings = (data: any) =>
    request<{ message: string; audit_log_id?: string }>("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── AI Assistant ─────────────────────────── */
export interface AiCatalogModel { id: string; label: string; }
export interface AiCatalogProvider {
    provider: string;
    label: string;
    key_field: string;
    models: AiCatalogModel[];
}
export const getAiCatalog = () =>
    request<{ providers: AiCatalogProvider[] }>("/api/admin/ai/catalog");

/* Super-admin AI console — chat as a user + view their threads. */
export interface AdminAiChatResponse {
    conversation_id: string;
    message_id: string;
    reply: string;
    actions: any[];
    audience: string;
}
export const adminAiChat = (data: {
    user_id: string;
    message: string;
    conversation_id?: string | null;
    audience?: "rider" | "driver";
}) =>
    request<AdminAiChatResponse>("/api/admin/ai/chat", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const getAdminAiConversations = (userId: string) =>
    request<{ conversations: { id: string; title: string; updated_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations`,
    );
export const getAdminAiMessages = (userId: string, conversationId: string) =>
    request<{ messages: { id: string; role: "user" | "assistant"; content: string; created_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations/${conversationId}/messages`,
    );

