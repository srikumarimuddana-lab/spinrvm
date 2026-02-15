import { Platform } from 'react-native';
import { appCache, CACHE_CONFIG, CACHE_KEYS } from '../cache';
import SpinrConfig from '../config/spinr.config';

// Endpoints that should be cached with their TTL
const CACHED_ENDPOINTS: Array<{
    pattern: RegExp;
    ttl: number;
    keyFn?: (url: string) => string;
}> = [
        // Document requirements - long TTL (static data)
        { pattern: /\/api\/drivers\/requirements$/, ttl: CACHE_CONFIG.DOCUMENT_REQUIREMENTS_TTL },
        // Driver documents
        { pattern: /\/api\/drivers\/documents$/, ttl: CACHE_CONFIG.DRIVER_DOCUMENTS_TTL },
        // Vehicle types - static reference data
        { pattern: /\/api\/vehicle-types$/, ttl: CACHE_CONFIG.DOCUMENT_REQUIREMENTS_TTL },
        // Pricing rules - relatively static
        { pattern: /\/api\/pricing$/, ttl: CACHE_CONFIG.DOCUMENT_REQUIREMENTS_TTL },
        // Service areas - static reference data
        { pattern: /\/api\/service-areas$/, ttl: CACHE_CONFIG.DOCUMENT_REQUIREMENTS_TTL },
    ];

const API_URL = SpinrConfig.backendUrl;

console.log('Cached API Client configured with URL:', API_URL);

// Helper to get stored token
const getStoredToken = async (): Promise<string | null> => {
    try {
        if (Platform.OS === 'web' && typeof window !== 'undefined') {
            return localStorage.getItem('auth_token');
        } else {
            const SecureStore = require('expo-secure-store');
            return await SecureStore.getItemAsync('auth_token');
        }
    } catch (e) { }
    return null;
};

// Helper to get auth header
const getAuthHeader = async (): Promise<string | null> => {
    try {
        // Try Firebase first
        try {
            const { auth } = require('../config/firebaseConfig');
            if (auth?.currentUser) {
                return await auth.currentUser.getIdToken();
            }
        } catch (firebaseError) {
            // Firebase not available, fall through to stored token
        }
        // Backend JWT flow — use stored token
        return await getStoredToken();
    } catch (error) {
        console.error('Error getting auth token:', error);
        return null;
    }
};

// Base fetch client with auth
const fetchWithAuth = async (url: string, options: RequestInit = {}): Promise<Response> => {
    const token = await getAuthHeader();
    const headers: Record<string, string> = {
        'Content-Type': 'application/json',
        ...(options.headers as Record<string, string>),
    };
    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    return fetch(`${API_URL}${url}`, {
        ...options,
        headers,
    });
};

/**
 * Extended client with caching methods using fetch
 */
export const cachedClient = {
    /**
     * GET request with optional caching
     */
    async get<T>(
        url: string,
        config?: {
            useCache?: boolean;
            cacheTTL?: number;
            forceRefresh?: boolean;
            headers?: Record<string, string>;
        }
    ): Promise<T> {
        const { useCache = true, cacheTTL, forceRefresh = false, headers } = config || {};

        if (!useCache) {
            const response = await fetchWithAuth(url, { method: 'GET', headers });
            if (!response.ok) {
                const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
                const error: any = new Error(errorData.detail || 'Request failed');
                error.response = { data: errorData, status: response.status };
                throw error;
            }
            return response.json();
        }

        const cacheKey = `cache:api:${url}`;

        // Check cache first (unless force refresh)
        if (!forceRefresh) {
            const cachedData = await appCache.get<T>(cacheKey);
            if (cachedData !== null) {
                console.log(`[APIClient] Cache hit: ${url}`);
                return cachedData;
            }
        }

        // Fetch from API
        console.log(`[APIClient] Fetching: ${url}`);
        const response = await fetchWithAuth(url, { method: 'GET', headers });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }

        const data = await response.json();

        // Store in cache
        await appCache.set(cacheKey, data, cacheTTL);

        return data;
    },

    /**
     * POST request (not cached)
     */
    async post<T>(url: string, data?: any, config?: { headers?: Record<string, string> }): Promise<T> {
        const response = await fetchWithAuth(url, {
            method: 'POST',
            body: data ? JSON.stringify(data) : undefined,
            headers: config?.headers,
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }

        return response.json();
    },

    /**
     * PUT request (not cached)
     */
    async put<T>(url: string, data?: any, config?: { headers?: Record<string, string> }): Promise<T> {
        const response = await fetchWithAuth(url, {
            method: 'PUT',
            body: data ? JSON.stringify(data) : undefined,
            headers: config?.headers,
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }

        return response.json();
    },

    /**
     * DELETE request (not cached)
     */
    async delete<T>(url: string, config?: { headers?: Record<string, string> }): Promise<T> {
        const response = await fetchWithAuth(url, {
            method: 'DELETE',
            headers: config?.headers,
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }

        return response.json().catch(() => ({} as T));
    },

    /**
     * Invalidate cache for specific URL pattern
     */
    async invalidateCache(urlPattern?: string): Promise<void> {
        if (urlPattern) {
            // Clear specific cache
            const cacheKey = `cache:api:${urlPattern}`;
            await appCache.remove(cacheKey);
        } else {
            // Clear all user-related cache
            await appCache.clearUserCache();
        }
        console.log(`[APIClient] Cache invalidated${urlPattern ? `: ${urlPattern}` : ''}`);
    },

    /**
     * Clear all cache
     */
    async clearCache(): Promise<void> {
        await appCache.clear();
        console.log('[APIClient] All cache cleared');
    },
};

// Export a default client object similar to the regular client
const client = {
    async get<T = any>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
        const response = await fetchWithAuth(url, { method: 'GET', headers: config?.headers });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }
        return { data: await response.json(), status: response.status };
    },

    async post<T = any>(url: string, body?: any, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
        const response = await fetchWithAuth(url, {
            method: 'POST',
            body: body ? JSON.stringify(body) : undefined,
            headers: config?.headers,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }
        return { data: await response.json(), status: response.status };
    },

    async put<T = any>(url: string, body?: any, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
        const response = await fetchWithAuth(url, {
            method: 'PUT',
            body: body ? JSON.stringify(body) : undefined,
            headers: config?.headers,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }
        return { data: await response.json(), status: response.status };
    },

    async delete<T = any>(url: string, config?: { headers?: Record<string, string> }): Promise<{ data: T; status: number }> {
        const response = await fetchWithAuth(url, {
            method: 'DELETE',
            headers: config?.headers,
        });
        if (!response.ok) {
            const errorData = await response.json().catch(() => ({ detail: 'Request failed' }));
            const error: any = new Error(errorData.detail || 'Request failed');
            error.response = { data: errorData, status: response.status };
            throw error;
        }
        return { data: await response.json().catch(() => ({} as T)), status: response.status };
    },
};

export default client;
