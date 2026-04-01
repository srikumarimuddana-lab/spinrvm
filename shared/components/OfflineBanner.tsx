import React, { useEffect, useState, useCallback } from 'react';
import { View, Text, StyleSheet, Animated, Platform } from 'react-native';
import NetInfo from '@react-native-community/netinfo';
import SpinrConfig from '../config/spinr.config';

interface OfflineBannerProps {
  visible?: boolean;
  onVisibilityChange?: (isVisible: boolean) => void;
}

/**
 * Offline Banner component that displays a banner when the device loses network connectivity.
 * 
 * Features:
 * - Automatic network status detection
 * - Smooth slide-in/slide-out animation
 * - Customizable appearance
 * - Optional visibility callback
 * 
 * Usage:
 * ```tsx
 * // In your root layout
 * <OfflineBanner />
 * 
 * // Or with callbacks
 * <OfflineBanner onVisibilityChange={(visible) => console.log('Banner:', visible)} />
 * ```
 */
export function OfflineBanner({ 
  visible: propVisible, 
  onVisibilityChange 
}: OfflineBannerProps) {
  const [isOffline, setIsOffline] = useState(false);
  const slideAnim = useState(new Animated.Value(-100))[0];

  const updateNetworkStatus = useCallback((isConnected: boolean | null) => {
    const offline = isConnected === false;
    setIsOffline(offline);
    
    // Animate banner
    Animated.timing(slideAnim, {
      toValue: offline ? 0 : -100,
      duration: 300,
      useNativeDriver: true,
    }).start();
    
    // Notify parent of visibility change
    onVisibilityChange?.(offline);
  }, [onVisibilityChange, slideAnim]);

  useEffect(() => {
    // Subscribe to network status changes
    const unsubscribe = NetInfo.addEventListener(state => {
      console.log('[OfflineBanner] Network state changed:', {
        isConnected: state.isConnected,
        isInternetReachable: state.isInternetReachable,
        type: state.type,
      });
      updateNetworkStatus(state.isConnected ?? state.isInternetReachable);
    });

    // Get initial network status
    NetInfo.fetch().then(state => {
      updateNetworkStatus(state.isConnected ?? state.isInternetReachable);
    });

    return () => {
      unsubscribe();
    };
  }, [updateNetworkStatus]);

  // If explicitly controlled via prop
  const isVisible = propVisible !== undefined ? propVisible : isOffline;

  if (!isVisible && !isOffline) {
    // Don't render if not visible and not animating
    return null;
  }

  return (
    <Animated.View 
      style={[
        styles.container,
        { transform: [{ translateY: slideAnim }] }
      ]}
      pointerEvents="box-none"
    >
      <View style={styles.content}>
        <Text style={styles.icon}>📡</Text>
        <Text style={styles.message}>No Internet Connection</Text>
        <Text style={styles.submessage}>Some features may be unavailable</Text>
      </View>
    </Animated.View>
  );
}

/**
 * Hook to check network status.
 * 
 * Usage:
 * ```tsx
 * const { isConnected, isOffline, networkType } = useNetworkStatus();
 * 
 * if (isOffline) {
 *   // Show offline UI
 * }
 * ```
 */
export function useNetworkStatus() {
  const [networkState, setNetworkState] = useState({
    isConnected: true,
    isOffline: false,
    networkType: 'unknown' as string,
    isInternetReachable: true as boolean | null,
  });

  useEffect(() => {
    const unsubscribe = NetInfo.addEventListener(state => {
      const isConnected = state.isConnected ?? state.isInternetReachable ?? false;
      setNetworkState({
        isConnected,
        isOffline: !isConnected,
        networkType: state.type || 'unknown',
        isInternetReachable: state.isInternetReachable,
      });
    });

    NetInfo.fetch().then(state => {
      const isConnected = state.isConnected ?? state.isInternetReachable ?? false;
      setNetworkState({
        isConnected,
        isOffline: !isConnected,
        networkType: state.type || 'unknown',
        isInternetReachable: state.isInternetReachable,
      });
    });

    return () => unsubscribe();
  }, []);

  return networkState;
}

/**
 * Higher-order component to wrap components with offline detection.
 * 
 * Usage:
 * ```tsx
 * const MyComponentWithOffline = withOfflineDetection(MyComponent);
 * ```
 */
export function withOfflineDetection<P extends object>(
  WrappedComponent: React.ComponentType<P & { isOffline: boolean }>
) {
  return function WithOfflineDetection(props: P) {
    const { isOffline } = useNetworkStatus();
    return <WrappedComponent {...props} isOffline={isOffline} />;
  };
}

const styles = StyleSheet.create({
  container: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    backgroundColor: SpinrConfig.theme.colors.error,
    zIndex: 9999,
    elevation: 10,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.25,
    shadowRadius: 3.84,
  },
  content: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 10,
    paddingHorizontal: 16,
    gap: 8,
  },
  icon: {
    fontSize: 16,
  },
  message: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '600',
  },
  submessage: {
    color: '#FFFFFF',
    fontSize: 12,
    opacity: 0.8,
  },
});

export default OfflineBanner;