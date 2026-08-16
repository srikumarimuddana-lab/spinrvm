import SpinrConfig from '../config/spinr.config';
import { getAuthHeader } from './client';

const EXT_TO_MIME: Record<string, string> = {
    jpg: 'image/jpeg', jpeg: 'image/jpeg',
    png: 'image/png',
    gif: 'image/gif',
    webp: 'image/webp',
    pdf: 'application/pdf',
    // iOS gallery assets keep a .HEIC filename even when expo-image-picker has
    // already re-encoded the bytes to JPEG (it does whenever `quality` is set).
    // Declaring image/jpeg matches the bytes in that common case; a genuine
    // HEIF file is caught by the backend's byte sniff and rejected with an
    // actionable message.
    heic: 'image/jpeg', heif: 'image/jpeg',
};

/**
 * Best-effort MIME type for a picked file.
 *
 * Pickers are unreliable sources for this: expo-image-picker's `asset.type` is
 * the media *category* ('image' | 'video'), never a MIME type, and Android's
 * document picker often reports 'application/octet-stream'. Prefer a type
 * derived from the file extension, and only fall back to what the picker said
 * when the extension tells us nothing.
 */
export function resolveUploadMimeType(nameOrUri: string, pickerType?: string | null): string {
    const ext = (nameOrUri || '').split('?')[0].split('.').pop()?.toLowerCase() || '';
    const fromExt = EXT_TO_MIME[ext];
    if (fromExt) return fromExt;
    // 'image'/'video' are expo-image-picker categories, not MIME types.
    if (pickerType && pickerType.includes('/') && pickerType !== 'application/octet-stream') {
        return pickerType;
    }
    return 'image/jpeg';
}

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
        // React Native's FormData.append accepts { uri, name, type } as a
        // file descriptor, but the standard TS lib only knows about Blob.
        // Double-cast via unknown to avoid any while keeping runtime behaviour.
        fd.append('file', {
            uri,
            name: name || 'upload',
            type: type || 'application/octet-stream',
        } as unknown as Blob);
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
