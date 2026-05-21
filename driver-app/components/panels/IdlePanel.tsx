import React, { useMemo } from 'react';
import { View, Text, TouchableOpacity, Animated, StyleSheet } from 'react-native';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

interface DriverData {
    is_online?: boolean;
    is_verified?: boolean;
    acceptance_rate?: string;
    total_rides?: string;
    vehicle_make?: string;
    license_plate?: string;
}

interface Earnings {
    total_earnings?: number;
}

interface IdlePanelProps {
    isOnline: boolean;
    driverData?: DriverData;
    earnings?: Earnings;
    onToggleOnline: () => void;
    pulseAnim: Animated.Value;
}

export const IdlePanel: React.FC<IdlePanelProps> = ({
    isOnline,
    driverData,
    earnings,
    onToggleOnline,
    pulseAnim,
}) => {
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const renderStatsRow = () => (
        <View style={styles.statsGrid}>
            <View style={styles.statBox} accessibilityRole="text" accessibilityLabel={`Acceptance rate ${driverData?.acceptance_rate || '100'} percent`}>
                <Text style={styles.statValue}>{driverData?.acceptance_rate || '100'}%</Text>
                <Text style={styles.statLabel}>Acceptance</Text>
            </View>
            <View style={styles.statBox} accessibilityRole="text" accessibilityLabel={`Earnings $${(earnings?.total_earnings || 0).toFixed(2)}`}>
                <Text style={styles.statValue}>${(earnings?.total_earnings || 0).toFixed(2)}</Text>
                <Text style={styles.statLabel}>Earnings</Text>
            </View>
            <View style={styles.statBox} accessibilityRole="text" accessibilityLabel={`${driverData?.total_rides || '0'} rides`}>
                <Text style={styles.statValue}>{driverData?.total_rides || '0'}</Text>
                <Text style={styles.statLabel}>Rides</Text>
            </View>
        </View>
    );

    return (
        <View style={styles.idlePanel}>
            <TouchableOpacity
                style={[
                    styles.onlineToggle,
                    !driverData?.is_verified ? styles.onlineDisabled : (isOnline ? styles.onlineActive : styles.onlineInactive)
                ]}
                onPress={onToggleOnline}
                activeOpacity={driverData?.is_verified ? 0.8 : 1}
                disabled={!driverData?.is_verified}
                accessibilityRole="switch"
                accessibilityLabel={!driverData?.is_verified ? 'Account Not Verified' : (isOnline ? "You're Online" : "You're Offline")}
                accessibilityState={{ checked: isOnline, disabled: !driverData?.is_verified }}
            >
                <Animated.View style={[styles.pulseIndicator, { transform: [{ scale: pulseAnim }] }]}>
                    <View style={[
                        styles.statusDot,
                        !driverData?.is_verified ? { backgroundColor: colors.orange } : (isOnline ? { backgroundColor: colors.success } : { backgroundColor: colors.error })
                    ]} />
                </Animated.View>
                <View style={styles.toggleText}>
                    <Text style={styles.toggleLabel}>
                        {!driverData?.is_verified ? 'Account Not Verified' : (isOnline ? 'You\'re Online' : 'You\'re Offline')}
                    </Text>
                    <Text style={styles.toggleSub}>
                        {!driverData?.is_verified
                            ? 'Complete your profile and wait for admin approval'
                            : (isOnline ? 'Waiting for ride requests...' : 'Go online to start earning')}
                    </Text>
                </View>
                <View style={[
                    styles.toggleSwitch,
                    !driverData?.is_verified ? styles.toggleSwitchDisabled : (isOnline && styles.toggleSwitchOn)
                ]}>
                    <View style={[
                        styles.toggleKnob,
                        !driverData?.is_verified ? styles.toggleKnobDisabled : (isOnline && styles.toggleKnobOn)
                    ]} />
                </View>
            </TouchableOpacity>

            {/* Stats Row */}
            {renderStatsRow()}
        </View>
    );
};

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    idlePanel: {
        position: 'absolute',
        bottom: 0,
        left: 0,
        right: 0,
        backgroundColor: colors.background,
        borderTopLeftRadius: 24,
        borderTopRightRadius: 24,
        paddingTop: 20,
        paddingBottom: 40,
        paddingHorizontal: 16,
    },
    onlineToggle: {
        flexDirection: 'row',
        alignItems: 'center',
        backgroundColor: colors.surface,
        borderRadius: 16,
        padding: 16,
        marginBottom: 20,
    },
    onlineDisabled: {
        opacity: 0.7,
    },
    onlineActive: {
        backgroundColor: colors.successBg,
    },
    onlineInactive: {
        backgroundColor: colors.surface,
    },
    pulseIndicator: {
        marginRight: 12,
    },
    statusDot: {
        width: 16,
        height: 16,
        borderRadius: 8,
    },
    toggleText: {
        flex: 1,
    },
    toggleLabel: {
        fontSize: 16,
        fontWeight: '600',
        color: colors.text,
    },
    toggleSub: {
        fontSize: 12,
        color: colors.textDim,
        marginTop: 2,
    },
    toggleSwitch: {
        width: 50,
        height: 30,
        borderRadius: 15,
        backgroundColor: colors.border,
        justifyContent: 'center',
        paddingHorizontal: 2,
    },
    toggleSwitchDisabled: {
        backgroundColor: colors.border,
    },
    toggleSwitchOn: {
        backgroundColor: colors.success,
    },
    toggleKnob: {
        width: 26,
        height: 26,
        borderRadius: 13,
        backgroundColor: '#fff',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 2 },
        shadowOpacity: 0.2,
        shadowRadius: 2,
        elevation: 2,
    },
    toggleKnobDisabled: {
        backgroundColor: colors.textDim,
    },
    toggleKnobOn: {
        alignSelf: 'flex-end',
    },
    statsGrid: {
        flexDirection: 'row',
        justifyContent: 'space-between',
    },
    statBox: {
        flex: 1,
        backgroundColor: colors.surface,
        borderRadius: 12,
        padding: 16,
        marginHorizontal: 4,
        alignItems: 'center',
    },
    statValue: {
        fontSize: 20,
        fontWeight: '700',
        color: colors.text,
    },
    statLabel: {
        fontSize: 12,
        color: colors.textDim,
        marginTop: 4,
    },
  });
}

export default IdlePanel;
