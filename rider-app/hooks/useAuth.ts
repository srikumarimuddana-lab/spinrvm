import { useEffect } from 'react'
import { useAuthStore } from '@shared/store/authStore'

interface UseAuthReturn {
  user: ReturnType<typeof useAuthStore>['user']
  loading: boolean
  isLoggedIn: boolean
  logout: () => Promise<void>
}

/**
 * Hook to manage auth state with HttpOnly cookie support.
 * Restores session on mount by calling initialize() which:
 * - Reads refresh_token from secure storage
 * - Calls /auth/refresh to get new access token (from HTTP-only cookie)
 * - Fetches current user from /auth/me
 */
export function useAuth(): UseAuthReturn {
  const { user, isInitialized, isLoading, logout } = useAuthStore()

  useEffect(() => {
    // Initialize auth on mount
    // This will restore session from HTTP-only cookies + secure storage
    useAuthStore.getState().initialize()
  }, [])

  return {
    user,
    loading: isLoading || !isInitialized,
    isLoggedIn: !!user,
    logout,
  }
}
