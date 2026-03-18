import React from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Image, Platform } from 'react-native';
import { useRouter } from 'expo-router';
import SpinrConfig from '../config/spinr.config';

interface ErrorScreenProps {
  title?: string;
  message?: string;
  error?: Error;
  onRetry?: () => void;
  onGoBack?: () => void;
  showGoBack?: boolean;
  icon?: 'error' | 'network' | 'notFound' | 'unauthorized';
}

/**
 * User-friendly error screen component for displaying errors in the mobile apps.
 * 
 * Usage:
 * ```tsx
 * <ErrorScreen 
 *   icon="network"
 *   message="Please check your connection and try again"
 *   onRetry={() => refreshData()}
 * />
 * ```
 */
export function ErrorScreen({
  title = 'Something went wrong',
  message,
  error,
  onRetry,
  onGoBack,
  showGoBack = true,
  icon = 'error',
}: ErrorScreenProps) {
  const router = useRouter();

  const handleGoBack = () => {
    if (onGoBack) {
      onGoBack();
    } else {
      router.back();
    }
  };

  const getIcon = () => {
    switch (icon) {
      case 'network':
        return '📡';
      case 'notFound':
        return '🔍';
      case 'unauthorized':
        return '🔒';
      default:
        return '⚠️';
    }
  };

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.icon}>{getIcon()}</Text>
        
        <Text style={styles.title}>{title}</Text>
        
        {message && (
          <Text style={styles.message}>{message}</Text>
        )}
        
        {error && __DEV__ && (
          <View style={styles.errorContainer}>
            <Text style={styles.errorText}>{error.message}</Text>
          </View>
        )}
        
        <View style={styles.buttonContainer}>
          {onRetry && (
            <TouchableOpacity 
              style={styles.retryButton} 
              onPress={onRetry}
              activeOpacity={0.7}
            >
              <Text style={styles.retryButtonText}>Try Again</Text>
            </TouchableOpacity>
          )}
          
          {showGoBack && (
            <TouchableOpacity 
              style={styles.backButton} 
              onPress={handleGoBack}
              activeOpacity={0.7}
            >
              <Text style={styles.backButtonText}>Go Back</Text>
            </TouchableOpacity>
          )}
        </View>
      </View>
    </View>
  );
}

/**
 * Network error screen - specialized for connectivity issues.
 */
export function NetworkErrorScreen({ onRetry }: { onRetry?: () => void }) {
  return (
    <ErrorScreen
      icon="network"
      title="No Internet Connection"
      message="Please check your network connection and try again."
      onRetry={onRetry}
      showGoBack={false}
    />
  );
}

/**
 * Not found screen - for 404 errors.
 */
export function NotFoundScreen({ message }: { message?: string }) {
  return (
    <ErrorScreen
      icon="notFound"
      title="Not Found"
      message={message || "The content you're looking for doesn't exist."}
      showGoBack={true}
    />
  );
}

/**
 * Unauthorized screen - for authentication/permission errors.
 */
export function UnauthorizedScreen({ onLogin }: { onLogin?: () => void }) {
  const router = useRouter();
  
  const handleLogin = () => {
    if (onLogin) {
      onLogin();
    } else {
      router.push('/login' as any);
    }
  };
  
  return (
    <ErrorScreen
      icon="unauthorized"
      title="Access Denied"
      message="You need to be logged in to access this content."
      onRetry={handleLogin}
      showGoBack={false}
    />
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    justifyContent: 'center',
    alignItems: 'center',
    backgroundColor: SpinrConfig.theme.colors.background,
    padding: 20,
  },
  content: {
    alignItems: 'center',
    maxWidth: 320,
  },
  icon: {
    fontSize: 64,
    marginBottom: 24,
  },
  title: {
    fontSize: 24,
    fontWeight: '700',
    color: SpinrConfig.theme.colors.text,
    marginBottom: 12,
    textAlign: 'center',
  },
  message: {
    fontSize: 16,
    color: SpinrConfig.theme.colors.textSecondary,
    textAlign: 'center',
    marginBottom: 24,
    lineHeight: 24,
  },
  errorContainer: {
    backgroundColor: SpinrConfig.theme.colors.error + '15',
    padding: 12,
    borderRadius: 8,
    marginBottom: 24,
    width: '100%',
  },
  errorText: {
    fontSize: 12,
    color: SpinrConfig.theme.colors.error,
    fontFamily: Platform.OS === 'ios' ? 'Menlo' : 'monospace',
  },
  buttonContainer: {
    flexDirection: 'row',
    gap: 12,
    flexWrap: 'wrap',
    justifyContent: 'center',
  },
  retryButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingHorizontal: 32,
    paddingVertical: 14,
    borderRadius: 10,
    minWidth: 120,
    alignItems: 'center',
  },
  retryButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  backButton: {
    backgroundColor: SpinrConfig.theme.colors.surface,
    paddingHorizontal: 32,
    paddingVertical: 14,
    borderRadius: 10,
    minWidth: 120,
    alignItems: 'center',
    borderWidth: 1,
    borderColor: SpinrConfig.theme.colors.border,
  },
  backButtonText: {
    color: SpinrConfig.theme.colors.text,
    fontSize: 16,
    fontWeight: '500',
  },
});

export default ErrorScreen;