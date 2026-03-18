import React, { Component, type ErrorInfo, type ReactNode } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, Platform } from 'react-native';
import SpinrConfig from '../config/spinr.config';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
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
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = {
      hasError: false,
      error: null,
      errorInfo: null,
    };
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
    try {
      // In production, send error to your error tracking service
      if (__DEV__) {
        console.error('[ErrorBoundary] Error details:', {
          message: error.message,
          stack: error.stack,
          componentStack: errorInfo.componentStack,
        });
      } else {
        // Production error reporting
        // Example: Sentry.captureException(error, { extra: { componentStack: errorInfo.componentStack } });
        console.error('[ErrorBoundary] Production error logged');
      }
    } catch (reportError) {
      console.error('[ErrorBoundary] Failed to report error:', reportError);
    }
  }

  handleRetry = (): void => {
    this.setState({
      hasError: false,
      error: null,
      errorInfo: null,
    });
  };

  render(): ReactNode {
    if (this.state.hasError) {
      // Use custom fallback if provided
      if (this.props.fallback) {
        return this.props.fallback;
      }

      // Default error UI
      return (
        <View style={styles.container}>
          <View style={styles.content}>
            <Text style={styles.icon}>⚠️</Text>
            <Text style={styles.title}>Something went wrong</Text>
            <Text style={styles.message}>
              {this.state.error?.message || 'An unexpected error occurred'}
            </Text>
            <TouchableOpacity style={styles.retryButton} onPress={this.handleRetry}>
              <Text style={styles.retryButtonText}>Try Again</Text>
            </TouchableOpacity>
          </View>
        </View>
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
    maxWidth: 300,
  },
  icon: {
    fontSize: 48,
    marginBottom: 16,
  },
  title: {
    fontSize: 20,
    fontWeight: '600',
    color: SpinrConfig.theme.colors.text,
    marginBottom: 8,
    textAlign: 'center',
  },
  message: {
    fontSize: 14,
    color: SpinrConfig.theme.colors.textSecondary,
    textAlign: 'center',
    marginBottom: 24,
    lineHeight: 20,
  },
  retryButton: {
    backgroundColor: SpinrConfig.theme.colors.primary,
    paddingHorizontal: 32,
    paddingVertical: 12,
    borderRadius: 8,
  },
  retryButtonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
});

export default ErrorBoundary;