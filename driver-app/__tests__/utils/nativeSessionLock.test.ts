/** @jest-environment node */
import { DatabaseSync } from 'node:sqlite';
import { mkdtempSync, rmSync, readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createNativeSessionLock } from '../../utils/nativeSessionLock';

jest.mock('expo-sqlite', () => ({ openDatabaseAsync: jest.fn() }));

it('serializes two independent runtimes against the actual SQLite file', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'spinr-session-'));
  const open = async () => {
    const db = new DatabaseSync(join(dir, 'lock.db'));
    return { execAsync: async (sql: string) => { db.exec(sql); }, closeAsync: async () => db.close() };
  };
  const firstRuntime = createNativeSessionLock(open);
  const secondRuntime = createNativeSessionLock(open);
  const order: string[] = [];
  let release!: () => void;
  const paused = new Promise<void>(resolve => { release = resolve; });
  let entered!: () => void;
  const started = new Promise<void>(resolve => { entered = resolve; });
  try {
    const first = firstRuntime(async () => { order.push('first'); entered(); await paused; order.push('saved'); });
    await started;
    const second = secondRuntime(async () => { order.push('second'); return 'renewed'; });
    await new Promise(resolve => setTimeout(resolve, 80));
    expect(order).toEqual(['first']);
    release();
    await first;
    expect(await second).toBe('renewed');
    expect(order).toEqual(['first', 'saved', 'second']);
    await expect(firstRuntime(async () => { throw new Error('request failed'); })).rejects.toThrow('request failed');
    expect(await secondRuntime(async () => 'released')).toBe('released');
  } finally { release(); rmSync(dir, { recursive: true, force: true }); }
});

it('never runs credential work if the database cannot be opened', async () => {
  const lock = createNativeSessionLock(async () => { throw new Error('storage unavailable'); });
  let ran = false;
  await expect(lock(async () => { ran = true; })).rejects.toThrow('storage unavailable');
  expect(ran).toBe(false);
});

it('defers on contention instead of taking over a suspended owner', async () => {
  jest.useFakeTimers();
  let ran = false;
  let closed = false;
  const lock = createNativeSessionLock(async () => ({
    execAsync: async sql => { if (sql === 'BEGIN IMMEDIATE') throw new Error('database is locked'); },
    closeAsync: async () => { closed = true; },
  }));
  try {
    const result = expect(lock(async () => { ran = true; })).rejects.toThrow('database is locked');
    await jest.advanceTimersByTimeAsync(10_100);
    await result;
    expect(ran).toBe(false);
    expect(closed).toBe(true);
  } finally { jest.useRealTimers(); }
});

it('installs native session exclusion before loading headless handlers or the UI', () => {
  const events: string[] = [];
  runInNewContext(readFileSync(join(__dirname, '../../index.js'), 'utf8'), {
    require: (name: string) => {
      if (name === './utils/nativeSessionLock') return { installNativeSessionCoordination: () => events.push('session-lock') };
      if (name === './services/backgroundMessaging') {
        events.push('handlers-loaded');
        return { registerBackgroundMessageHandlers: () => events.push('handlers-registered') };
      }
      if (name === 'react-native') return { Platform: { OS: 'ios' } };
      if (name === 'expo-router/entry') { events.push('ui'); return {}; }
      throw new Error(`Unexpected entry dependency ${name}`);
    },
  });
  expect(events).toEqual(['session-lock', 'handlers-loaded', 'handlers-registered', 'ui']);
});
