import React, { Component, useMemo, type ErrorInfo, type ReactNode } from 'react';
import { AppState, View, Text, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import { router } from 'expo-router';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';
import { captureException } from '../services/errorReporting';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
  retryCount: number;
}

// ── Functional fallback UI ────────────────────────────────────────────────────
// Extracted so it can consume useTheme (hooks cannot be called in class components).

import { ScrollView } from 'react-native';

interface ErrorFallbackProps {
  error: Error | null;
  componentStack?: string | null;
  onRetry: () => void;
}

function ErrorFallback({ error, componentStack, onRetry }: ErrorFallbackProps) {
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  // The React component stack names the exact element chain down to the
  // invalid render (e.g. "Element type is invalid … got: object"). Show it
  // on-device — not just in __DEV__ — so a release/TestFlight crash can be
  // diagnosed from a screenshot when Sentry isn't reachable.
  const diagnostics = (componentStack || error?.stack || '').trim();

  return (
    <View style={styles.container}>
      <View style={styles.content}>
        <Text style={styles.icon}>⚠️</Text>
        <Text style={styles.title}>Something went wrong</Text>
        <Text style={styles.message} selectable>
          {error?.name ? `${error.name}: ` : ''}{error?.message || 'An unexpected error occurred'}
        </Text>

        {diagnostics.length > 0 && (
          <ScrollView style={styles.stackScroll} contentContainerStyle={styles.stackContent}>
            <Text style={styles.stackText} selectable>{diagnostics}</Text>
          </ScrollView>
        )}

        <TouchableOpacity style={styles.retryButton} onPress={onRetry}>
          <Text style={styles.retryButtonText}>Try Again</Text>
        </TouchableOpacity>
      </View>
    </View>
  );
}

/**
 * Error Boundary component to catch and handle React errors gracefully.
 *
 * Usage:
 * ```tsx
 * <ErrorBoundary fallback={<CustomFallback />}>
 *   <YourComponent />
 * </ErrorBoundary>
 * ```
 */
export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  private appStateSub: ReturnType<typeof AppState.addEventListener> | null = null;

  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
      retryCount: 0,
    };
  }

  componentDidMount(): void {
    this.appStateSub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active' && this.state.hasError) {
        this.reloadApp();
      }
    });
  }

  componentWillUnmount(): void {
    this.appStateSub?.remove();
  }

  static getDerivedStateFromError(error: Error): Partial<ErrorBoundaryState> {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error('[ErrorBoundary] Caught error:', error, errorInfo);

    this.setState({ errorInfo } as ErrorBoundaryState);

    // Log to error reporting service (Sentry, etc.)
    void this.reportError(error, errorInfo);

    // Call parent error handler if provided
    this.props.onError?.(error, errorInfo);
  }

  async reportError(error: Error, errorInfo: ErrorInfo): Promise<void> {
    captureException(error, {
      componentStack: errorInfo.componentStack ?? '',
    });
  }

  handleRetry = (): void => {
    const nextCount = this.state.retryCount + 1;
    if (nextCount >= 2) {
      this.reloadApp();
      return;
    }
    this.setState({ hasError: false, error: null, errorInfo: null, retryCount: nextCount }, () => {
      try { router.replace('/(tabs)' as any); } catch { /* navigation not ready */ }
    });
  };

  reloadApp = async (): Promise<void> => {
    try {
      const Updates = require('expo-updates');
      await Updates.reloadAsync();
    } catch {
      this.setState({ hasError: false, error: null, errorInfo: null, retryCount: 0 }, () => {
        try { router.replace('/(tabs)' as any); } catch {}
      });
    }
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // Default error UI — rendered via functional component to access useTheme
      return (
        <ErrorFallback
          error={this.state.error}
          componentStack={this.state.errorInfo?.componentStack ?? null}
          onRetry={this.handleRetry}
        />
      );
    }

    return this.props.children;
  }
}

/**
 * Functional error boundary hook for use in functional components.
 *
 * Note: This is a simplified version that works with React's error handling.
 * For full error boundary functionality, use the class-based ErrorBoundary.
 */
export function useErrorBoundary() {
  const [error, setError] = React.useState<Error | null>(null);

  const showError = (err: Error): void => {
    console.error('[useErrorBoundary]', err);
    setError(err);
  };

  const clearError = (): void => {
    setError(null);
  };

  return { error, showError, clearError };
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      justifyContent: 'center',
      alignItems: 'center',
      backgroundColor: colors.background,
      padding: 20,
    },
    content: {
      alignItems: 'center',
      maxWidth: 300,
    },
    icon: {
      fontSize: 48,
      marginBottom: 16,
    },
    title: {
      fontSize: 20,
      fontWeight: '600',
      color: colors.text,
      marginBottom: 8,
      textAlign: 'center',
    },
    message: {
      fontSize: 14,
      color: colors.textSecondary,
      textAlign: 'center',
      marginBottom: 24,
      lineHeight: 20,
    },
    retryButton: {
      backgroundColor: colors.primary,
      paddingHorizontal: 32,
      paddingVertical: 12,
      borderRadius: 8,
    },
    retryButtonText: {
      color: '#FFFFFF',
      fontSize: 16,
      fontWeight: '600',
    },
    stackScroll: {
      maxHeight: 320,
      width: 300,
      backgroundColor: '#1E1E1E',
      borderRadius: 6,
      padding: 10,
      marginBottom: 20,
    },
    stackContent: {
      flexGrow: 1,
    },
    stackText: {
      color: '#FF6B6B',
      fontFamily: Platform.OS === 'ios' ? 'Courier New' : 'monospace',
      fontSize: 11,
    },
  });
}

export default ErrorBoundary;
