import SpinrConfig from '../config/spinr.config';
import { getAuthHeader } from './client';

/**
 * Uploads a file to the backend server.
 * @param uri The local URI of the file.
 * @param name The file name.
 * @param type The MIME type of the file.
 * @returns The release URL (e.g. /uploads/filename.jpg)
 */
export async function uploadFile(uri: string, name: string, type: string): Promise<string> {
    // Build a fresh FormData per attempt — React Native consumes the
    // body on the first send and passing the same instance into a retry
    // would fire an empty body.
    const buildFormData = () => {
        const fd = new FormData();
        fd.append('file', {
            uri,
            name: name || 'upload',
            type: type || 'application/octet-stream',
        } as any);
        return fd;
    };

    const doUpload = async (token: string | null): Promise<Response> => {
        // We do NOT set Content-Type header so that the engine sets the boundary correctly.
        return fetch(`${SpinrConfig.backendUrl}/api/v1/upload`, {
            method: 'POST',
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            body: buildFormData(),
        });
    };

    try {
        // Auth: /api/v1/upload is behind get_current_user, so we MUST attach
        // the access token. getAuthHeader resolves from the in-memory store
        // (see shared/api/client.ts) because authStore.setTokens no longer
        // persists access tokens to SecureStore.
        //
        // Raw fetch skips the axios silent-refresh interceptor, so we
        // replicate the refresh-and-retry-once dance here. Without it an
        // access token that expired mid-session would surface as
        // "No authorization token provided" instead of transparently
        // recovering the way every other API call does.
        let token = await getAuthHeader();
        if (!token) {
            const { useAuthStore } = require('../store/authStore');
            const refreshed = await useAuthStore.getState().refreshTokens();
            token = refreshed ? await getAuthHeader() : null;
            if (!token) {
                throw new Error('Your session has expired. Please log in again.');
            }
        }

        let response = await doUpload(token);

        if (response.status === 401) {
            const { useAuthStore } = require('../store/authStore');
            const refreshed = await useAuthStore.getState().refreshTokens();
            if (refreshed) {
                const freshToken = await getAuthHeader();
                if (freshToken) {
                    response = await doUpload(freshToken);
                }
            }
        }

        if (!response.ok) {
            const text = await response.text();
            throw new Error(`Upload failed: ${response.status} ${text}`);
        }

        const data = await response.json();
        return data.url;
    } catch (error) {
        console.error('File upload error:', error);
        throw error;
    }
}
