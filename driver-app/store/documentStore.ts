import { create } from 'zustand';
import api from '@shared/api/client';
import { appCache, CACHE_KEYS, CACHE_CONFIG } from '@shared/cache';

export interface DocumentRequirement {
    id: string;
    name: string;
    description?: string;
    is_mandatory: boolean;
    requires_back_side: boolean;
    created_at: string;
}

export interface DriverDocument {
    id: string;
    driver_id: string;
    requirement_id?: string;
    document_type: string;
    document_url: string;
    side?: string;
    status: string;
    rejection_reason?: string;
    uploaded_at: string;
}

interface DocumentState {
    requirements: DocumentRequirement[];
    documents: DriverDocument[];
    isLoading: boolean;
    error: string | null;

    // Actions
    fetchRequirements: (forceRefresh?: boolean) => Promise<void>;
    fetchDocuments: (driverId: string, forceRefresh?: boolean) => Promise<void>;
    uploadDocument: (
        driverId: string,
        requirementId: string,
        file: any,
        side?: string
    ) => Promise<DriverDocument>;
    deleteDocument: (documentId: string) => Promise<void>;
    clearError: () => void;
    clearCache: () => Promise<void>;
}

export const useDocumentStore = create<DocumentState>((set, get) => ({
    requirements: [],
    documents: [],
    isLoading: false,
    error: null,

    fetchRequirements: async (forceRefresh = false) => {
        set({ isLoading: true, error: null });

        try {
            // Try to get from cache first
            if (!forceRefresh) {
                const cached = await appCache.get<DocumentRequirement[]>(CACHE_KEYS.DOCUMENT_REQUIREMENTS);
                if (cached) {
                    console.log('[DocumentStore] Using cached requirements');
                    set({ requirements: cached, isLoading: false });
                    return;
                }
            }

            // Fetch from API
            const response = await api.get('/api/drivers/requirements');
            const requirements = response.data as DocumentRequirement[];

            // Cache the results
            await appCache.set(
                CACHE_KEYS.DOCUMENT_REQUIREMENTS,
                requirements,
                CACHE_CONFIG.DOCUMENT_REQUIREMENTS_TTL
            );

            set({ requirements, isLoading: false });
        } catch (error: any) {
            console.log('Failed to fetch document requirements:', error);
            set({
                error: error.response?.data?.detail || 'Failed to fetch requirements',
                isLoading: false
            });
        }
    },

    fetchDocuments: async (driverId: string, forceRefresh = false) => {
        set({ isLoading: true, error: null });

        try {
            const cacheKey = CACHE_KEYS.DRIVER_DOCUMENTS(driverId);

            // Try to get from cache first
            if (!forceRefresh) {
                const cached = await appCache.get<DriverDocument[]>(cacheKey);
                if (cached) {
                    console.log('[DocumentStore] Using cached documents');
                    set({ documents: cached, isLoading: false });
                    return;
                }
            }

            // Fetch from API
            const response = await api.get(`/drivers/documents`);
            const documents = response.data as DriverDocument[];

            // Cache the results
            await appCache.set(
                cacheKey,
                documents,
                CACHE_CONFIG.DRIVER_DOCUMENTS_TTL
            );

            set({ documents, isLoading: false });
        } catch (error: any) {
            console.log('Failed to fetch driver documents:', error);
            set({
                error: error.response?.data?.detail || 'Failed to fetch documents',
                isLoading: false
            });
        }
    },

    uploadDocument: async (
        driverId: string,
        requirementId: string,
        file: any,
        side?: string
    ) => {
        set({ isLoading: true, error: null });

        try {
            const formData = new FormData();
            formData.append('file', file);
            formData.append('driver_id', driverId);
            formData.append('requirement_id', requirementId);
            if (side) {
                formData.append('side', side);
            }

            const response = await api.post('/drivers/documents/upload', formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });

            const newDoc = response.data as DriverDocument;

            // Update local state
            const { documents } = get();
            set({ documents: [...documents, newDoc], isLoading: false });

            // Invalidate cache
            const cacheKey = CACHE_KEYS.DRIVER_DOCUMENTS(driverId);
            await appCache.remove(cacheKey);

            return newDoc;
        } catch (error: any) {
            console.log('Failed to upload document:', error);
            set({
                error: error.response?.data?.detail || 'Failed to upload document',
                isLoading: false
            });
            throw error;
        }
    },

    deleteDocument: async (documentId: string) => {
        set({ isLoading: true, error: null });

        try {
            await api.delete(`/drivers/documents/${documentId}`);

            // Update local state
            const { documents } = get();
            set({
                documents: documents.filter(d => d.id !== documentId),
                isLoading: false
            });

            // Note: Cache will expire naturally
        } catch (error: any) {
            console.log('Failed to delete document:', error);
            set({
                error: error.response?.data?.detail || 'Failed to delete document',
                isLoading: false
            });
        }
    },

    clearError: () => set({ error: null }),

    clearCache: async () => {
        await appCache.remove(CACHE_KEYS.DOCUMENT_REQUIREMENTS);
        set({ requirements: [], documents: [] });
    },
}));
