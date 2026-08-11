import { useEffect, useState } from 'react';
import api from '@shared/api/client';

export interface EmergencyContactSummary {
  id: string;
  name: string;
}

export interface UseEmergencyContactsResult {
  contacts: EmergencyContactSummary[];
  loading: boolean;
}

/**
 * Thin wrapper around GET /users/emergency-contacts (backend/routes/users.py),
 * shared by the driver Safety shield/overlay (ACTION_ITEMS.md B16) — both
 * need the caller's emergency-contact names for the "Alert Emergency
 * Contacts" subtitle and the silent-alert toast copy.
 *
 * Degrades to an empty array on any fetch failure rather than throwing —
 * the shield/overlay must still render (with generic "Spinr Safety
 * (silent)" copy, no names) instead of crashing a safety-critical surface
 * over a contacts-list fetch failure.
 */
export function useEmergencyContacts(): UseEmergencyContactsResult {
  const [contacts, setContacts] = useState<EmergencyContactSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const res = await api.get<{ contacts: Array<{ id: string; name: string }> }>('/users/emergency-contacts');
        if (!cancelled) {
          setContacts((res.data?.contacts || []).map((c) => ({ id: c.id, name: c.name })));
        }
      } catch {
        if (!cancelled) setContacts([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  return { contacts, loading };
}
