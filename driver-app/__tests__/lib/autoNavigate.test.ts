/**
 * Once-per-leg guard for the automatic navigation hand-off.
 *
 * The case that matters is the cold start: the driver spends the whole drive to
 * pickup inside Google Maps, Android reclaims the backgrounded Spinr process,
 * and reopening Spinr to tap "Arrived" remounts the panel. Without the durable
 * marker that remount re-fires the hand-off and bounces them back out to Maps.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { claimAutoNavLeg, _resetAutoNavClaimForTest } from '../../lib/navigation/autoNavigate';

const getItem = AsyncStorage.getItem as jest.Mock;
const setItem = AsyncStorage.setItem as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  _resetAutoNavClaimForTest();
  getItem.mockResolvedValue(null);
  setItem.mockResolvedValue(undefined);
});

describe('claimAutoNavLeg', () => {
  it('claims an unseen leg and records it', async () => {
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(true);
    expect(setItem).toHaveBeenCalledWith('@spinr_auto_nav_launched', 'ride-1:pickup');
  });

  it('refuses a second claim on the same leg within the session', async () => {
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(true);
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(false);
  });

  it('refuses a leg already marked before this session — the cold-start case', async () => {
    getItem.mockResolvedValue('ride-1:pickup');
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(false);
    expect(setItem).not.toHaveBeenCalled();
  });

  it('still allows the dropoff leg after the pickup leg was claimed', async () => {
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(true);
    await expect(claimAutoNavLeg('ride-1', 'dropoff')).resolves.toBe(true);
  });

  it('allows the next ride after the previous one was claimed', async () => {
    getItem.mockResolvedValue('ride-1:dropoff');
    await expect(claimAutoNavLeg('ride-2', 'pickup')).resolves.toBe(true);
  });

  it('only one of two concurrent claims on the same leg wins', async () => {
    const [a, b] = await Promise.all([
      claimAutoNavLeg('ride-1', 'pickup'),
      claimAutoNavLeg('ride-1', 'pickup'),
    ]);
    expect([a, b].filter(Boolean)).toHaveLength(1);
  });

  it('launches anyway when storage is unavailable, rather than never launching', async () => {
    getItem.mockRejectedValue(new Error('storage unavailable'));
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(true);
  });

  it('does not repeat within the session when storage is unavailable', async () => {
    getItem.mockRejectedValue(new Error('storage unavailable'));
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(true);
    await expect(claimAutoNavLeg('ride-1', 'pickup')).resolves.toBe(false);
  });
});
