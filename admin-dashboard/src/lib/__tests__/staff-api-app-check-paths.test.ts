/**
 * Regression guard: staff API calls must target /api/admin/*, never /api/v1/*.
 *
 * The backend's Firebase App Check middleware exempts /api/admin/ (this
 * browser app can't attach an X-Firebase-AppCheck header) but enforces
 * /api/v1/. The Quests page and "reset surge to auto" called /api/v1/ paths,
 * got 401 "App Check token required" in production, and request()'s 401
 * handler treated that as an expired session and logged the admin out.
 */

import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('@/store/authStore', () => ({
  useAuthStore: {
    getState: () => ({ token: 'test-token', csrfToken: 'csrf', logout: vi.fn() }),
  },
}));

const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

import {
  getQuests,
  createQuest,
  updateQuest,
  getQuestParticipants,
  resetSurgeToAuto,
} from '@/lib/api';

describe('staff API paths avoid the App-Check-enforced /api/v1/ namespace', () => {
  beforeEach(() => {
    mockFetch.mockReset();
    mockFetch.mockResolvedValue({ ok: true, status: 200, json: () => Promise.resolve([]) });
  });

  it.each([
    ['getQuests', () => getQuests(), '/api/admin/quests/list', 'GET'],
    ['createQuest', () => createQuest({ title: 't' }), '/api/admin/quests/create', 'POST'],
    ['updateQuest', () => updateQuest('q1', { is_active: false }), '/api/admin/quests/q1', 'PATCH'],
    ['getQuestParticipants', () => getQuestParticipants('q1'), '/api/admin/quests/q1/participants', 'GET'],
    ['resetSurgeToAuto', () => resetSurgeToAuto('a1'), '/api/admin/service-areas/a1/surge/auto', 'PUT'],
  ])('%s calls %s', async (_name, call, url, method) => {
    await call();
    const [calledUrl, init] = mockFetch.mock.calls[0];
    expect(calledUrl).toBe(url);
    expect((init?.method ?? 'GET').toUpperCase()).toBe(method);
  });

  it('no staff API module under src/lib/api/ references /api/v1/', () => {
    const dir = join(__dirname, '..', 'api');
    const offenders = readdirSync(dir)
      .filter((f) => f.endsWith('.ts'))
      .filter((f) => readFileSync(join(dir, f), 'utf8').includes('/api/v1/'));
    expect(offenders).toEqual([]);
  });
});
