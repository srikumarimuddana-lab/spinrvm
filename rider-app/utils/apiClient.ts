import axios, { AxiosError } from 'axios'
import { useAuthStore } from '@shared/store/authStore'

const BACKEND_URL = process.env.EXPO_PUBLIC_BACKEND_URL || 'http://localhost:8000'

// axios's default export IS the configured instance this file needs
// (.create(), .get, ...); the rule's suggested `import { create } from
// 'axios'` would drop the instance methods (interceptors,
// apiClient.get/.post/...) this file relies on everywhere else.
// eslint-disable-next-line import/no-named-as-default-member
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
        useAuthStore.getState().logout()
        // Redirect to login
        return Promise.reject(refreshError)
      }
    }

    return Promise.reject(error)
  }
)

export default apiClient
