import React, { useState } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    ScrollView,
    Platform,
    Switch,
    Alert,
} from 'react-native';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';

import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;
const COLORS = {
    primary: THEME.background, // Background (white)
    accent: THEME.primary, // Action/brand color (red)
    accentDim: THEME.primaryDark,
    surface: THEME.surface,
    surfaceLight: THEME.surfaceLight,
    text: THEME.text,
    textDim: THEME.textDim,
    success: THEME.success,
    gold: '#FFD700',
    orange: '#FF9500',
    danger: THEME.error,
    border: THEME.border,
};

export default function SettingsScreen() {
    const router = useRouter();
    const { logout } = useAuthStore();

    // Preference states (local)
    const [pushNotifications, setPushNotifications] = useState(true);
    const [rideAlerts, setRideAlerts] = useState(true);
    const [earningsSummary, setEarningsSummary] = useState(true);
    const [promotions, setPromotions] = useState(false);
    const [soundEffects, setSoundEffects] = useState(true);
    const [vibration, setVibration] = useState(true);
    const [navApp, setNavApp] = useState<'default' | 'google' | 'waze'>('default');

    const handleLogout = () => {
        Alert.alert('Sign Out', 'Are you sure you want to sign out?', [
            { text: 'Cancel', style: 'cancel' },
            {
                text: 'Sign Out',
                style: 'destructive',
                onPress: () => {
                    logout();
                    router.replace('/');
                },
            },
        ]);
    };

    const handleDeleteAccount = () => {
        Alert.alert(
            'Delete Account',
            'This action is permanent. All your data, earnings history, and account information will be deleted. Are you sure?',
            [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Delete', style: 'destructive', onPress: () => { } },
            ]
        );
    };

    const renderToggle = (
        label: string,
        description: string,
        value: boolean,
        onToggle: (val: boolean) => void,
        icon: string,
        iconColor: string = COLORS.accent,
    ) => (
        <View style={styles.settingRow}>
            <View style={[styles.settingIcon, { backgroundColor: `${iconColor}12` }]}>
                <Ionicons name={icon as any} size={18} color={iconColor} />
            </View>
            <View style={{ flex: 1 }}>
                <Text style={styles.settingLabel}>{label}</Text>
                <Text style={styles.settingDesc}>{description}</Text>
            </View>
            <Switch
                value={value}
                onValueChange={onToggle}
                trackColor={{ false: COLORS.surfaceLight, true: COLORS.accentDim }}
                thumbColor={value ? COLORS.accent : '#ccc'}
            />
        </View>
    );

    return (
        <View style={styles.container}>
            {/* Header */}
            <LinearGradient colors={[COLORS.surface, COLORS.primary]} style={styles.header}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={COLORS.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Settings</Text>
                    <View style={{ width: 40 }} />
                </View>
            </LinearGradient>

            <ScrollView contentContainerStyle={{ paddingBottom: 100 }} showsVerticalScrollIndicator={false}>
                {/* Notifications Section */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Notifications</Text>
                    <View style={styles.card}>
                        {renderToggle('Push Notifications', 'Receive real-time notifications', pushNotifications, setPushNotifications, 'notifications', COLORS.accent)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Ride Alerts', 'Sound and vibration for new rides', rideAlerts, setRideAlerts, 'car', COLORS.orange)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Daily Earnings Summary', 'Get notified about daily earnings', earningsSummary, setEarningsSummary, 'wallet', COLORS.gold)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Promotions & Offers', 'Special offers and promotions', promotions, setPromotions, 'gift', COLORS.accentDim)}
                    </View>
                </View>

                {/* Sound & Haptics */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Sound & Haptics</Text>
                    <View style={styles.card}>
                        {renderToggle('Sound Effects', 'Play sounds for ride events', soundEffects, setSoundEffects, 'volume-high', COLORS.accent)}
                        <View style={styles.cardDivider} />
                        {renderToggle('Vibration', 'Haptic feedback for new rides', vibration, setVibration, 'phone-portrait', COLORS.orange)}
                    </View>
                </View>

                {/* Navigation */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Navigation</Text>
                    <View style={styles.card}>
                        {(['default', 'google', 'waze'] as const).map((app, i) => (
                            <React.Fragment key={app}>
                                {i > 0 && <View style={styles.cardDivider} />}
                                <TouchableOpacity
                                    style={styles.navOption}
                                    onPress={() => setNavApp(app)}
                                >
                                    <View style={[styles.settingIcon, { backgroundColor: 'rgba(0,212,170,0.08)' }]}>
                                        <Ionicons
                                            name={app === 'default' ? 'map' : app === 'google' ? 'navigate' : 'compass'}
                                            size={18}
                                            color={COLORS.accent}
                                        />
                                    </View>
                                    <Text style={styles.settingLabel}>
                                        {app === 'default' ? 'Default Maps' : app === 'google' ? 'Google Maps' : 'Waze'}
                                    </Text>
                                    <View style={[styles.radio, navApp === app && styles.radioActive]}>
                                        {navApp === app && <View style={styles.radioInner} />}
                                    </View>
                                </TouchableOpacity>
                            </React.Fragment>
                        ))}
                    </View>
                </View>

                {/* Account */}
                <View style={styles.section}>
                    <Text style={styles.sectionTitle}>Account</Text>
                    <View style={styles.card}>
                        <TouchableOpacity style={styles.actionRow}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(0,212,170,0.08)' }]}>
                                <Ionicons name="language" size={18} color={COLORS.accent} />
                            </View>
                            <Text style={styles.settingLabel}>Language</Text>
                            <Text style={styles.settingValue}>English</Text>
                            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(0,212,170,0.08)' }]}>
                                <Ionicons name="document-text" size={18} color={COLORS.accent} />
                            </View>
                            <Text style={styles.settingLabel}>Terms of Service</Text>
                            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
                        </TouchableOpacity>
                        <View style={styles.cardDivider} />
                        <TouchableOpacity style={styles.actionRow}>
                            <View style={[styles.settingIcon, { backgroundColor: 'rgba(0,212,170,0.08)' }]}>
                                <Ionicons name="shield" size={18} color={COLORS.accent} />
                            </View>
                            <Text style={styles.settingLabel}>Privacy Policy</Text>
                            <Ionicons name="chevron-forward" size={18} color={COLORS.textDim} />
                        </TouchableOpacity>
                    </View>
                </View>

                {/* Danger Zone */}
                <View style={styles.section}>
                    <TouchableOpacity style={styles.logoutBtn} onPress={handleLogout}>
                        <Ionicons name="log-out" size={20} color={COLORS.danger} />
                        <Text style={styles.logoutText}>Sign Out</Text>
                    </TouchableOpacity>

                    <TouchableOpacity style={styles.deleteBtn} onPress={handleDeleteAccount}>
                        <Text style={styles.deleteText}>Delete Account</Text>
                    </TouchableOpacity>
                </View>

                {/* App Version */}
                <Text style={styles.version}>Spinr Driver v1.0.0</Text>
            </ScrollView>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: COLORS.primary },
    header: {
        paddingTop: Platform.OS === 'ios' ? 55 : 35,
        paddingBottom: 12,
        paddingHorizontal: 16,
    },
    headerRow: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
    },
    backBtn: {
        width: 40,
        height: 40,
        borderRadius: 20,
        backgroundColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
    },
    headerTitle: { color: COLORS.text, fontSize: 20, fontWeight: '700' },
    section: { paddingHorizontal: 16, marginTop: 20 },
    sectionTitle: {
        color: COLORS.text,
        fontSize: 16,
        fontWeight: '700',
        marginBottom: 10,
    },
    card: {
        backgroundColor: COLORS.surface,
        borderRadius: 18,
        padding: 4,
        borderWidth: 1,
        borderColor: 'rgba(255,255,255,0.04)',
    },
    cardDivider: {
        height: 1,
        backgroundColor: COLORS.surfaceLight,
        marginHorizontal: 14,
    },
    settingRow: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        padding: 14,
    },
    settingIcon: {
        width: 36,
        height: 36,
        borderRadius: 10,
        justifyContent: 'center',
        alignItems: 'center',
    },
    settingLabel: { color: COLORS.text, fontSize: 14, fontWeight: '500' },
    settingDesc: { color: COLORS.textDim, fontSize: 11, marginTop: 1 },
    settingValue: {
        color: COLORS.textDim,
        fontSize: 13,
        marginLeft: 'auto',
        marginRight: 6,
    },
    navOption: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        padding: 14,
    },
    radio: {
        width: 22,
        height: 22,
        borderRadius: 11,
        borderWidth: 2,
        borderColor: COLORS.surfaceLight,
        justifyContent: 'center',
        alignItems: 'center',
        marginLeft: 'auto',
    },
    radioActive: { borderColor: COLORS.accent },
    radioInner: {
        width: 12,
        height: 12,
        borderRadius: 6,
        backgroundColor: COLORS.accent,
    },
    actionRow: {
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        padding: 14,
    },
    logoutBtn: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 8,
        padding: 16,
        backgroundColor: 'rgba(255,71,87,0.08)',
        borderRadius: 16,
        borderWidth: 1,
        borderColor: 'rgba(255,71,87,0.2)',
    },
    logoutText: { color: COLORS.danger, fontSize: 16, fontWeight: '600' },
    deleteBtn: { alignItems: 'center', marginTop: 14, padding: 10 },
    deleteText: { color: COLORS.textDim, fontSize: 13 },
    version: {
        color: COLORS.surfaceLight,
        textAlign: 'center',
        fontSize: 12,
        marginTop: 20,
        marginBottom: 20,
    },
});
