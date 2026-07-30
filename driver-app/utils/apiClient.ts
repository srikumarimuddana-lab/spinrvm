import axios, { AxiosError } from 'axios'
import { useAuthStore } from '@shared/store/authStore'

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://localhost:8000'

const apiClient = axios.create({
  baseURL: BACKEND_URL,
  timeout: 10000,
  withCredentials: true,  // ← AUTO-SEND COOKIES
  headers: {
    'Content-Type': 'application/json',
  },
})

/**
 * Response interceptor: Handle 401 (expired auth token)
 * Trigger auto-refresh + retry
 */
apiClient.interceptors.response.use(
  (response) => response,
  async (error: AxiosError) => {
    const originalRequest = error.config

    // If 401 and we haven't already tried refreshing
    if (
      error.response?.status === 401 &&
      originalRequest &&
      !originalRequest.headers['X-Retry-Attempted']
    ) {
      try {
        // POST /auth/refresh
        // Browser auto-sends refresh_token cookie
        await apiClient.post('/auth/refresh')

        // Mark as attempted (prevent infinite loop)
        originalRequest.headers['X-Retry-Attempted'] = 'true'

        // Retry original request
        // Browser auto-sends new auth_token cookie
        return apiClient.request(originalRequest)
      } catch (refreshError) {
        // Refresh failed: token invalid, force logout
        // Refresh already failed — the credential is dead, nothing to revoke.
        await useAuthStore.getState().logout({ revokeServerSession: false })
        // Redirect to login will happen via auth state change
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default apiClient
