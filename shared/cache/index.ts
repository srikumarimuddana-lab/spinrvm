import { Platform } from 'react-native';

// Cache configuration
const CACHE_CONFIG = {
    // Default TTL in milliseconds (5 minutes)
    DEFAULT_TTL: 5 * 60 * 1000,
    // Longer TTL for static data like document requirements (1 hour)
    DOCUMENT_REQUIREMENTS_TTL: 60 * 60 * 1000,
    // TTL for user profile data (2 minutes)
    USER_PROFILE_TTL: 2 * 60 * 1000,
    // TTL for driver documents (5 minutes)
    DRIVER_DOCUMENTS_TTL: 5 * 60 * 1000,
    // TTL for images (30 minutes - images don't change often)
    IMAGE_TTL: 30 * 60 * 1000,
};

// Storage interface for cross-platform compatibility
interface CacheStorage {
    getItem(key: string): Promise<string | null>;
    setItem(key: string, value: string): Promise<void>;
    removeItem(key: string): Promise<void>;
}

// Platform-specific storage implementation
const createStorage = (): CacheStorage => {
    if (Platform.OS === 'web' && typeof window !== 'undefined') {
        return {
            getItem: async (key: string) => localStorage.getItem(key),
            setItem: async (key: string, value: string) => localStorage.setItem(key, value),
            removeItem: async (key: string) => localStorage.removeItem(key),
        };
    } else {
        // Use AsyncStorage for React Native
        try {
            const AsyncStorage = require('@react-native-async-storage/async-storage').default;
            return {
                getItem: async (key: string) => AsyncStorage.getItem(key),
                setItem: async (key: string, value: string) => AsyncStorage.setItem(key, value),
                removeItem: async (key: string) => AsyncStorage.removeItem(key),
            };
        } catch (e) {
            // Fallback to a simple in-memory cache if storage is not available
            console.warn('AsyncStorage not available, using in-memory cache');
            const memoryCache: Record<string, { value: string; expiry: number }> = {};
            return {
                getItem: async (key: string) => {
                    const item = memoryCache[key];
                    if (item && item.expiry > Date.now()) {
                        return item.value;
                    }
                    return null;
                },
                setItem: async (key: string, value: string) => {
                    memoryCache[key] = { value, expiry: Date.now() + CACHE_CONFIG.DEFAULT_TTL };
                },
                removeItem: async (key: string) => {
                    delete memoryCache[key];
                },
            };
        }
    }
};

const storage = createStorage();

// Cache entry structure
interface CacheEntry<T> {
    data: T;
    timestamp: number;
    ttl: number;
}

// Cache keys helper
export const CACHE_KEYS = {
    USER_PROFILE: 'cache:user_profile',
    DRIVER_PROFILE: 'cache:driver_profile',
    DOCUMENT_REQUIREMENTS: 'cache:document_requirements',
    DRIVER_DOCUMENTS: (driverId: string) => `cache:driver_documents:${driverId}`,
    USER_IMAGE: (userId: string) => `cache:user_image:${userId}`,
    DRIVER_IMAGE: (driverId: string) => `cache:driver_image:${driverId}`,
    VEHICLE_TYPES: 'cache:vehicle_types',
    PRICING_RULES: 'cache:pricing_rules',
    SERVICE_AREAS: 'cache:service_areas',
};

// Main cache class
class AppCache {
    private memoryCache: Map<string, { data: any; expiry: number }> = new Map();

    /**
     * Get data from cache
     */
    async get<T>(key: string): Promise<T | null> {
        try {
            // First try memory cache (fastest)
            const memoryEntry = this.memoryCache.get(key);
            if (memoryEntry && memoryEntry.expiry > Date.now()) {
                console.log(`[Cache] Memory hit: ${key}`);
                return memoryEntry.data as T;
            }

            // Try persistent storage
            const stored = await storage.getItem(key);
            if (stored) {
                const entry: CacheEntry<T> = JSON.parse(stored);

                // Check if expired
                if (entry.timestamp + entry.ttl < Date.now()) {
                    console.log(`[Cache] Expired: ${key}`);
                    await this.remove(key);
                    return null;
                }

                console.log(`[Cache] Storage hit: ${key}`);
                // Restore to memory cache
                this.memoryCache.set(key, {
                    data: entry.data,
                    expiry: entry.timestamp + entry.ttl,
                });
                return entry.data as T;
            }

            console.log(`[Cache] Miss: ${key}`);
            return null;
        } catch (error) {
            console.error(`[Cache] Error getting ${key}:`, error);
            return null;
        }
    }

    /**
     * Set data in cache
     */
    async set<T>(key: string, data: T, ttl?: number): Promise<void> {
        try {
            const entry: CacheEntry<T> = {
                data,
                timestamp: Date.now(),
                ttl: ttl || CACHE_CONFIG.DEFAULT_TTL,
            };

            // Store in memory cache
            this.memoryCache.set(key, {
                data: entry.data,
                expiry: entry.timestamp + entry.ttl,
            });

            // Store in persistent storage
            await storage.setItem(key, JSON.stringify(entry));
            console.log(`[Cache] Set: ${key} (TTL: ${entry.ttl}ms)`);
        } catch (error) {
            console.error(`[Cache] Error setting ${key}:`, error);
        }
    }

    /**
     * Remove data from cache
     */
    async remove(key: string): Promise<void> {
        try {
            this.memoryCache.delete(key);
            await storage.removeItem(key);
            console.log(`[Cache] Removed: ${key}`);
        } catch (error) {
            console.error(`[Cache] Error removing ${key}:`, error);
        }
    }

    /**
     * Clear all cache
     */
    async clear(): Promise<void> {
        try {
            this.memoryCache.clear();
            // Note: For full clear, we'd need to iterate through all keys
            // This is handled by logout which removes specific keys
            console.log('[Cache] Cleared memory cache');
        } catch (error) {
            console.error('[Cache] Error clearing cache:', error);
        }
    }

    /**
     * Clear user-specific cache (on logout)
     */
    async clearUserCache(): Promise<void> {
        try {
            const keysToRemove: string[] = [];

            // Collect keys to remove from memory
            for (const key of this.memoryCache.keys()) {
                if (key.startsWith('cache:user_') || key.startsWith('cache:driver_')) {
                    keysToRemove.push(key);
                }
            }

            // Remove from memory and storage
            for (const key of keysToRemove) {
                await this.remove(key);
            }

            console.log('[Cache] Cleared user cache');
        } catch (error) {
            console.error('[Cache] Error clearing user cache:', error);
        }
    }

    /**
     * Check if cache exists and is valid
     */
    async has(key: string): Promise<boolean> {
        const data = await this.get(key);
        return data !== null;
    }

    /**
     * Get cached data or fetch from API
     */
    async getOrFetch<T>(
        key: string,
        fetchFn: () => Promise<T>,
        ttl?: number
    ): Promise<T> {
        // Try to get from cache first
        const cached = await this.get<T>(key);
        if (cached !== null) {
            return cached;
        }

        // Fetch from API
        const data = await fetchFn();

        // Store in cache
        await this.set(key, data, ttl);

        return data;
    }
}

// Export singleton instance
export const appCache = new AppCache();

// Export configuration for external use
export { CACHE_CONFIG };
export type { CacheEntry, CacheStorage };
