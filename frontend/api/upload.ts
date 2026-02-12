import SpinrConfig from '../config/spinr.config';

/**
 * Uploads a file to the backend server.
 * @param uri The local URI of the file.
 * @param name The file name.
 * @param type The MIME type of the file.
 * @returns The release URL (e.g. /uploads/filename.jpg)
 */
export async function uploadFile(uri: string, name: string, type: string): Promise<string> {
    const formData = new FormData();
    // specific shape for React Native FormData
    formData.append('file', {
        uri,
        name: name || 'upload',
        type: type || 'application/octet-stream',
    } as any);

    try {
        // We do NOT set Content-Type header so that the browser/engine sets the boundary correctly
        const response = await fetch(`${SpinrConfig.backendUrl}/api/upload`, {
            method: 'POST',
            body: formData,
        });

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
