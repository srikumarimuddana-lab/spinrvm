"use client";

import React, { createContext, useContext, useEffect, useState } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { loginAdmin, sendOtp } from '@/lib/api'; // Import the API functions

// Helper functions to manage token in localStorage
const TOKEN_KEY = 'admin_token'; // Use the same token key as in api.ts

const getToken = () => {
  if (typeof window !== 'undefined') {
    return localStorage.getItem(TOKEN_KEY);
  }
  return null;
};

const setToken = (token: string) => {
  if (typeof window !== 'undefined') {
    localStorage.setItem(TOKEN_KEY, token);
  }
};

const removeToken = () => {
  if (typeof window !== 'undefined') {
    localStorage.removeItem(TOKEN_KEY);
  }
};

// Function to add auth header to requests
const authFetch = async (url: string, options: RequestInit = {}) => {
  const token = getToken();
  const headers = {
    'Content-Type': 'application/json',
    ...options.headers,
  };

  if (token) {
    (headers as Record<string, string>)['Authorization'] = `Bearer ${token}`;
  }

  return fetch(url, {
    ...options,
    headers,
  });
};

interface AuthContextType {
  user: any;
  loading: boolean;
  login: (phone: string, otp: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export const AuthProvider = ({ children }: { children: React.ReactNode }): React.ReactElement => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  useEffect(() => {
    // Check if user is authenticated
    const checkAuth = async () => {
      try {
        const token = getToken();
        if (!token) {
          setLoading(false);
          return;
        }

        console.log('Checking auth with token:', token.substring(0, 20) + '...');
        const res = await authFetch('/api/auth/me');
        console.log('Auth check response:', res.status, res.statusText);
        
        if (res.ok) {
          const data = await res.json();
          console.log('Auth user data:', data);
          setUser(data);
        } else {
          console.log('Auth check failed, removing token');
          // Token might be invalid/expired, remove it
          removeToken();
        }
      } catch (error) {
        console.error('Auth check failed:', error);
        removeToken(); // Remove invalid token
      } finally {
        setLoading(false);
      }
    };

    checkAuth();
  }, []);

  const login = async (phone: string, otp: string) => {
    // Use the same API function as the login page for consistency
    const data = await loginAdmin(phone, otp);
    
    // The token is already stored by the loginAdmin function
    // Now we need to fetch the user profile to update the user state
    const profileRes = await authFetch('/api/auth/me');
    if (profileRes.ok) {
      const userData = await profileRes.json();
      setUser(userData);
    }
    
    router.push('/dashboard');
  };

  const logout = async () => {
    // Clear user data and token
    setUser(null);
    removeToken();
    router.push('/login');
  };

  return (
    <AuthContext.Provider value={{ user, loading, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}

export const RequireAuth = ({ children }: { children: React.ReactNode }): React.ReactElement | null => {
  const { user, loading } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  // Public routes that don't require authentication
  const publicRoutes = ['/login', '/register'];
  const isPublicRoute = publicRoutes.some(route => pathname === route || pathname.startsWith(route + '/'));

  useEffect(() => {
    // If this is a public route, don't redirect - just render children
    if (isPublicRoute) {
      return;
    }
    if (!loading && !user) {
      router.push('/login');
    }
  }, [user, loading, router, isPublicRoute]);

  // If still loading, show loading spinner
  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen bg-background">
        <div className="text-center">
          <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary mx-auto mb-4"></div>
          <p>Loading...</p>
        </div>
      </div>
    );
  }

  // If no user and not a public route, don't render children (will redirect)
  if (!user && !isPublicRoute) {
    return null;
  }

  return <>{children}</>;
}
