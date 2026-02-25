import React from 'react';
import { View, Text, TouchableOpacity, StyleSheet } from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import SpinrConfig from '@shared/config/spinr.config';

const COLORS = SpinrConfig.theme.colors;

interface DriverTopBarProps {
    driverName: string;
    vehicleInfo: string;
    isOnline: boolean;
    onMenuPress?: () => void;
}

export const DriverTopBar: React.FC<DriverTopBarProps> = ({
    driverName,
    vehicleInfo,
    isOnline,
    onMenuPress,
}) => {
    return (
        <View style={styles.topBar}>
            <View style={styles.topBarInner}>
                <View style={styles.driverInfo}>
                    <TouchableOpacity style={styles.avatarSmall} onPress={onMenuPress}>
                        <Ionicons name="person" size={20} color={COLORS.text} />
                    </TouchableOpacity>
                    <View>
                        <Text style={styles.driverName}>{driverName}</Text>
                        <Text style={styles.vehicleInfo}>{vehicleInfo}</Text>
                    </View>
                </View>
                <View style={styles.onlineBadge}>
                    <View style={[styles.statusDotSmall, isOnline ? styles.onlineDot : styles.offlineDot]} />
                    <Text style={[styles.onlineBadgeText, isOnline && { color: COLORS.success }]}>
                        {isOnline ? 'ONLINE' : 'OFFLINE'}
                    </Text>
                </View>
            </View>
        </View>
    );
};

const styles = StyleSheet.create({
    topBar: {
        position: 'absolute',
        top: 50,
        left: 16,
        right: 16,
        zIndex: 10,
    },
    topBarInner: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        backgroundColor: COLORS.overlay,
        borderRadius: 16,
        padding: 12,
        paddingHorizontal: 16,
    },
    driverInfo: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 10,
    },
    avatarSmall: {
        width: 36,
        height: 36,
        borderRadius: 18,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    driverName: {
        color: COLORS.text,
        fontSize: 14,
        fontWeight: '600',
    },
    vehicleInfo: {
        color: COLORS.textDim,
        fontSize: 11,
    },
    onlineBadge: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
    },
    statusDotSmall: {
        width: 8,
        height: 8,
        borderRadius: 4,
    },
    onlineDot: {
        backgroundColor: COLORS.success,
    },
    offlineDot: {
        backgroundColor: COLORS.danger,
    },
    onlineBadgeText: {
        fontSize: 11,
        fontWeight: '700',
        color: COLORS.textDim,
        letterSpacing: 0.5,
    },
});

export default DriverTopBar;
