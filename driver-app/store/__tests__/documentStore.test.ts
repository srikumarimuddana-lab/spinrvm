/**
 * Unit tests for documentStore — covers fetchRequirements, fetchDocuments,
 * uploadDocument, deleteDocument, clearError, clearCache.
 *
 * Coverage target: P3-4 step 2 (raise driver-app coverage to next threshold).
 */

import { useDocumentStore } from '../documentStore';
import api from '@shared/api/client';
import { appCache } from '@shared/cache';

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

// Stub the appCache so tests don't touch real storage
jest.mock('@shared/cache', () => ({
    appCache: {
        get: jest.fn(() => Promise.resolve(null)),
        set: jest.fn(() => Promise.resolve()),
        remove: jest.fn(() => Promise.resolve()),
    },
    CACHE_KEYS: {
        DOCUMENT_REQUIREMENTS: 'doc_requirements',
        DRIVER_DOCUMENTS: (id: string) => `driver_docs_${id}`,
    },
    CACHE_CONFIG: {
        DOCUMENT_REQUIREMENTS_TTL: 3600,
        DRIVER_DOCUMENTS_TTL: 3600,
    },
}));

const mockGet = api.get as jest.Mock;
const mockPost = api.post as jest.Mock;
const mockDelete = api.delete as jest.Mock;
const mockCacheGet = appCache.get as jest.Mock;
const mockCacheSet = appCache.set as jest.Mock;
const mockCacheRemove = appCache.remove as jest.Mock;

beforeEach(() => {
    jest.clearAllMocks();
    // Reset store state between tests
    useDocumentStore.setState({ requirements: [], documents: [], isLoading: false, error: null });
});

describe('documentStore — fetchRequirements', () => {
    const reqs = [{ id: 'r1', name: 'License', is_mandatory: true, requires_back_side: false, created_at: '2026-01-01' }];

    it('fetches from API and caches when cache is empty', async () => {
        mockCacheGet.mockResolvedValue(null);
        mockGet.mockResolvedValue({ data: reqs });

        await useDocumentStore.getState().fetchRequirements();

        expect(mockGet).toHaveBeenCalledWith('/drivers/requirements');
        expect(mockCacheSet).toHaveBeenCalled();
        expect(useDocumentStore.getState().requirements).toEqual(reqs);
        expect(useDocumentStore.getState().isLoading).toBe(false);
    });

    it('returns cached data and skips API call', async () => {
        mockCacheGet.mockResolvedValue(reqs);

        await useDocumentStore.getState().fetchRequirements();

        expect(mockGet).not.toHaveBeenCalled();
        expect(useDocumentStore.getState().requirements).toEqual(reqs);
    });

    it('bypasses cache and hits API on forceRefresh=true', async () => {
        mockCacheGet.mockResolvedValue(reqs);
        mockGet.mockResolvedValue({ data: [] });

        await useDocumentStore.getState().fetchRequirements(true);

        expect(mockGet).toHaveBeenCalled();
    });

    it('sets error on API failure', async () => {
        mockCacheGet.mockResolvedValue(null);
        mockGet.mockRejectedValue({ response: { data: { detail: 'Server error' } } });

        await useDocumentStore.getState().fetchRequirements();

        expect(useDocumentStore.getState().error).toBe('Server error');
        expect(useDocumentStore.getState().isLoading).toBe(false);
    });
});

describe('documentStore — fetchDocuments', () => {
    const docs = [{ id: 'd1', driver_id: 'drv1', document_type: 'license', document_url: 'url', status: 'approved', uploaded_at: '2026-01-01' }];

    it('fetches from API and caches when cache is empty', async () => {
        mockCacheGet.mockResolvedValue(null);
        mockGet.mockResolvedValue({ data: docs });

        await useDocumentStore.getState().fetchDocuments('drv1');

        expect(mockGet).toHaveBeenCalledWith('/drivers/documents');
        expect(useDocumentStore.getState().documents).toEqual(docs);
        expect(useDocumentStore.getState().isLoading).toBe(false);
    });

    it('returns cached documents without hitting API', async () => {
        mockCacheGet.mockResolvedValue(docs);

        await useDocumentStore.getState().fetchDocuments('drv1');

        expect(mockGet).not.toHaveBeenCalled();
        expect(useDocumentStore.getState().documents).toEqual(docs);
    });

    it('sets error state on API failure', async () => {
        mockCacheGet.mockResolvedValue(null);
        mockGet.mockRejectedValue({ response: { data: { detail: 'Fetch failed' } } });

        await useDocumentStore.getState().fetchDocuments('drv1');

        expect(useDocumentStore.getState().error).toBe('Fetch failed');
    });
});

describe('documentStore — uploadDocument', () => {
    const newDoc = { id: 'doc-new', driver_id: 'drv1', document_type: 'license', document_url: 'url2', status: 'pending', uploaded_at: '2026-01-01' };

    it('posts to upload endpoint and appends doc to state', async () => {
        mockPost.mockResolvedValue({ data: newDoc });

        const result = await useDocumentStore.getState().uploadDocument('drv1', 'req1', { uri: 'file://img.jpg' });

        expect(mockPost).toHaveBeenCalledWith(
            '/drivers/documents/upload',
            expect.any(FormData)
        );
        expect(result).toEqual(newDoc);
        expect(useDocumentStore.getState().documents).toContainEqual(newDoc);
        expect(mockCacheRemove).toHaveBeenCalled();
    });

    it('includes side in FormData when provided', async () => {
        mockPost.mockResolvedValue({ data: newDoc });
        const appendSpy = jest.spyOn(FormData.prototype, 'append');

        await useDocumentStore.getState().uploadDocument('drv1', 'req1', { uri: 'file://back.jpg' }, 'back');

        expect(appendSpy).toHaveBeenCalledWith('side', 'back');
    });

    it('sets error and rethrows on upload failure', async () => {
        mockPost.mockRejectedValue({ response: { data: { detail: 'Upload failed' } } });

        await expect(
            useDocumentStore.getState().uploadDocument('drv1', 'req1', {})
        ).rejects.toBeTruthy();

        expect(useDocumentStore.getState().error).toBe('Upload failed');
        expect(useDocumentStore.getState().isLoading).toBe(false);
    });
});

describe('documentStore — deleteDocument', () => {
    it('removes document from state after successful delete', async () => {
        const existing = { id: 'doc-del', driver_id: 'drv1', document_type: 'license', document_url: 'url', status: 'approved', uploaded_at: '2026-01-01' };
        useDocumentStore.setState({ documents: [existing] });
        mockDelete.mockResolvedValue({});

        await useDocumentStore.getState().deleteDocument('doc-del');

        expect(mockDelete).toHaveBeenCalledWith('/drivers/documents/doc-del');
        expect(useDocumentStore.getState().documents).toHaveLength(0);
    });

    it('sets error on delete failure without rethrowing', async () => {
        mockDelete.mockRejectedValue({ response: { data: { detail: 'Not found' } } });

        await useDocumentStore.getState().deleteDocument('missing');

        expect(useDocumentStore.getState().error).toBe('Not found');
    });
});

describe('documentStore — clearError and clearCache', () => {
    it('clearError resets error to null', () => {
        useDocumentStore.setState({ error: 'some error' });
        useDocumentStore.getState().clearError();
        expect(useDocumentStore.getState().error).toBeNull();
    });

    it('clearCache removes requirements key and resets state', async () => {
        useDocumentStore.setState({ requirements: [{ id: 'r1', name: 'X', is_mandatory: true, requires_back_side: false, created_at: '' }] });

        await useDocumentStore.getState().clearCache();

        expect(mockCacheRemove).toHaveBeenCalledWith('doc_requirements');
        expect(useDocumentStore.getState().requirements).toEqual([]);
        expect(useDocumentStore.getState().documents).toEqual([]);
    });
});
