import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import SettingsPage from '@/app/dashboard/settings/page';
import { getSettings, updateSettings } from '@/lib/api';

// 2026-09-25: Settings > Operations > "Rider & Driver Features" card —
// destination_mode_enabled (migration 482) and saved_place_shortcuts_enabled
// (migration 484).
vi.mock('@/lib/api', () => ({
  getSettings: vi.fn(),
  updateSettings: vi.fn(),
  mfaStatus: vi.fn().mockResolvedValue({ mfa_enabled: false, available: true, enforced: false }),
  mfaDisable: vi.fn(),
  adminUploadRideOfferSound: vi.fn(),
  getAiCatalog: vi.fn().mockResolvedValue({ providers: [] }),
  getEmailDeliverability: vi.fn().mockResolvedValue({}),
  getHeatMapSettings: vi.fn().mockResolvedValue({}),
  updateHeatMapSettings: vi.fn(),
}));
vi.mock('@/components/ui/use-toast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/components/mfa-enroll-dialog', () => ({ MfaEnrollDialog: () => null }));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(updateSettings).mockResolvedValue({ message: 'Settings updated' });
});

async function openOperations() {
  const user = userEvent.setup();
  render(<SettingsPage />);
  await user.click(await screen.findByRole('tab', { name: 'Operations' }));
  return user;
}

describe('Settings — rider & driver feature toggles', () => {
  it('reflects defaults: destination mode off, saved-place shortcuts on when the key is absent', async () => {
    vi.mocked(getSettings).mockResolvedValue({ destination_mode_enabled: false });
    await openOperations();
    expect(await screen.findByRole('switch', { name: 'Destination mode enabled' })).not.toBeChecked();
    expect(screen.getByRole('switch', { name: 'Saved-place shortcuts enabled' })).toBeChecked();
  });

  it('does not send saved_place_shortcuts_enabled on a save that never touched it', async () => {
    vi.mocked(getSettings).mockResolvedValue({ destination_mode_enabled: false });
    const user = await openOperations();
    await user.click(await screen.findByRole('switch', { name: 'Destination mode enabled' }));
    await user.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalled());
    const payload = vi.mocked(updateSettings).mock.calls[0][0];
    expect(payload.destination_mode_enabled).toBe(true);
    expect(payload).not.toHaveProperty('saved_place_shortcuts_enabled');
  });

  it('sends saved_place_shortcuts_enabled=false once an admin turns it off', async () => {
    vi.mocked(getSettings).mockResolvedValue({ destination_mode_enabled: true, saved_place_shortcuts_enabled: true });
    const user = await openOperations();
    expect(await screen.findByRole('switch', { name: 'Destination mode enabled' })).toBeChecked();
    await user.click(screen.getByRole('switch', { name: 'Saved-place shortcuts enabled' }));
    await user.click(screen.getByRole('button', { name: /save changes/i }));
    await waitFor(() => expect(updateSettings).toHaveBeenCalled());
    const payload = vi.mocked(updateSettings).mock.calls[0][0];
    expect(payload.saved_place_shortcuts_enabled).toBe(false);
    expect(payload.destination_mode_enabled).toBe(true);
  });
});
