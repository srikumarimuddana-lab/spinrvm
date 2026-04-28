/**
 * Unit tests for questStore — covers fetchAvailableQuests, fetchMyQuests,
 * joinQuest, claimReward, clearError.
 *
 * Coverage target: P3-4 step 2 (raise driver-app coverage to next threshold).
 */

jest.mock('react-native', () => ({
    Platform: { OS: 'ios' },
    Alert: { alert: jest.fn() },
    NativeModules: {},
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
    getItem: jest.fn(() => Promise.resolve(null)),
    setItem: jest.fn(() => Promise.resolve()),
    removeItem: jest.fn(() => Promise.resolve()),
}));

jest.mock('@shared/api/client', () => ({
    __esModule: true,
    default: {
        get: jest.fn(),
        post: jest.fn(),
        put: jest.fn(),
        patch: jest.fn(),
        delete: jest.fn(),
    },
}));

import { useQuestStore } from '../questStore';
import api from '@shared/api/client';

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;

const sampleQuest = {
    id: 'q1',
    title: '10 Rides',
    description: 'Complete 10 rides',
    type: 'rides_completed',
    target_value: 10,
    reward_amount: 25,
    reward_type: 'cash',
    start_date: '2026-01-01',
    end_date: '2026-01-31',
    current_value: 0,
    progress_pct: 0,
    status: 'available',
    progress_id: null,
};

const sampleProgress = {
    progress_id: 'p1',
    quest: {
        id: 'q1',
        title: '10 Rides',
        description: 'Complete 10 rides',
        type: 'rides_completed',
        target_value: 10,
        reward_amount: 25,
        reward_type: 'cash',
        start_date: '2026-01-01',
        end_date: '2026-01-31',
    },
    current_value: 5,
    progress_pct: 50,
    status: 'active',
    started_at: '2026-01-05',
    completed_at: null,
    claimed_at: null,
};

beforeEach(() => {
    jest.clearAllMocks();
    useQuestStore.setState({ availableQuests: [], myQuests: [], isLoading: false, error: null });
});

describe('questStore — fetchAvailableQuests', () => {
    it('fetches quests and stores them in state', async () => {
        mockGet.mockResolvedValue({ data: [sampleQuest] });

        await useQuestStore.getState().fetchAvailableQuests();

        expect(mockGet).toHaveBeenCalledWith('/quests');
        expect(useQuestStore.getState().availableQuests).toEqual([sampleQuest]);
        expect(useQuestStore.getState().isLoading).toBe(false);
        expect(useQuestStore.getState().error).toBeNull();
    });

    it('defaults to empty array when API returns null', async () => {
        mockGet.mockResolvedValue({ data: null });

        await useQuestStore.getState().fetchAvailableQuests();

        expect(useQuestStore.getState().availableQuests).toEqual([]);
    });

    it('sets error message on API failure', async () => {
        const err = new Error('Network error');
        mockGet.mockRejectedValue(err);

        await useQuestStore.getState().fetchAvailableQuests();

        expect(useQuestStore.getState().error).toBe('Network error');
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('sets isLoading during fetch', async () => {
        let resolveGet!: (v: any) => void;
        mockGet.mockReturnValue(new Promise((res) => { resolveGet = res; }));

        const fetchPromise = useQuestStore.getState().fetchAvailableQuests();
        expect(useQuestStore.getState().isLoading).toBe(true);

        resolveGet({ data: [sampleQuest] });
        await fetchPromise;
        expect(useQuestStore.getState().isLoading).toBe(false);
    });
});

describe('questStore — fetchMyQuests', () => {
    it('fetches my quests and stores them in state', async () => {
        mockGet.mockResolvedValue({ data: [sampleProgress] });

        await useQuestStore.getState().fetchMyQuests();

        expect(mockGet).toHaveBeenCalledWith('/quests/my-quests');
        expect(useQuestStore.getState().myQuests).toEqual([sampleProgress]);
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('defaults to empty array when API returns null', async () => {
        mockGet.mockResolvedValue({ data: null });

        await useQuestStore.getState().fetchMyQuests();

        expect(useQuestStore.getState().myQuests).toEqual([]);
    });

    it('sets error message on API failure', async () => {
        const err = new Error('Server error');
        mockGet.mockRejectedValue(err);

        await useQuestStore.getState().fetchMyQuests();

        expect(useQuestStore.getState().error).toBe('Server error');
        expect(useQuestStore.getState().isLoading).toBe(false);
    });
});

describe('questStore — joinQuest', () => {
    it('posts to join endpoint then refreshes both quest lists', async () => {
        mockPost.mockResolvedValue({});
        // GET calls: first = fetchAvailableQuests, second = fetchMyQuests
        mockGet
            .mockResolvedValueOnce({ data: [sampleQuest] })
            .mockResolvedValueOnce({ data: [sampleProgress] });

        await useQuestStore.getState().joinQuest('q1');

        expect(mockPost).toHaveBeenCalledWith('/quests/q1/join');
        expect(mockGet).toHaveBeenCalledWith('/quests');
        expect(mockGet).toHaveBeenCalledWith('/quests/my-quests');
        expect(useQuestStore.getState().availableQuests).toEqual([sampleQuest]);
        expect(useQuestStore.getState().myQuests).toEqual([sampleProgress]);
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('sets error from response.data.detail on API failure and rethrows', async () => {
        const err = { response: { data: { detail: 'Quest not found' } }, message: 'Request failed' };
        mockPost.mockRejectedValue(err);

        await expect(useQuestStore.getState().joinQuest('q-missing')).rejects.toEqual(err);

        expect(useQuestStore.getState().error).toBe('Quest not found');
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('falls back to error.message when response detail is absent', async () => {
        const err = new Error('Timeout');
        mockPost.mockRejectedValue(err);

        await expect(useQuestStore.getState().joinQuest('q1')).rejects.toThrow('Timeout');

        expect(useQuestStore.getState().error).toBe('Timeout');
    });
});

describe('questStore — claimReward', () => {
    it('posts to claim endpoint, refreshes myQuests, and returns reward data', async () => {
        const rewardData = { reward_amount: 25, message: 'Reward claimed!' };
        mockPost.mockResolvedValue({ data: rewardData });
        mockGet.mockResolvedValue({ data: [sampleProgress] });

        const result = await useQuestStore.getState().claimReward('p1');

        expect(mockPost).toHaveBeenCalledWith('/quests/progress/p1/claim');
        expect(mockGet).toHaveBeenCalledWith('/quests/my-quests');
        expect(result).toEqual(rewardData);
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('sets error from response.data.detail on failure and rethrows', async () => {
        const err = { response: { data: { detail: 'Already claimed' } }, message: 'Bad request' };
        mockPost.mockRejectedValue(err);

        await expect(useQuestStore.getState().claimReward('p1')).rejects.toEqual(err);

        expect(useQuestStore.getState().error).toBe('Already claimed');
        expect(useQuestStore.getState().isLoading).toBe(false);
    });

    it('falls back to error.message when response detail is absent', async () => {
        const err = new Error('Network failure');
        mockPost.mockRejectedValue(err);

        await expect(useQuestStore.getState().claimReward('p1')).rejects.toThrow('Network failure');

        expect(useQuestStore.getState().error).toBe('Network failure');
    });
});

describe('questStore — clearError', () => {
    it('resets error to null', () => {
        useQuestStore.setState({ error: 'some error' });
        useQuestStore.getState().clearError();
        expect(useQuestStore.getState().error).toBeNull();
    });
});
