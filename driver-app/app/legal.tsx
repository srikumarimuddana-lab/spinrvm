import React, { useEffect, useRef, useState, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    ScrollView,
    TouchableOpacity,
    ActivityIndicator,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useRouter, useLocalSearchParams } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { SpinrConfig } from '@shared/config/spinr.config';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const STATIC_PRIVACY_POLICY = `Spinr Technologies Inc. ("Spinr", "we", "us") is committed to protecting your privacy.

Information We Collect
We collect information you provide when creating an account (name, email, phone number), location data while the app is in use, device identifiers, and usage information to improve our services.

How We Use Your Information
Your information is used to facilitate ridesharing services, process payments, communicate with you, ensure safety, comply with legal obligations, and improve the platform.

Information Sharing
We share information with riders as necessary to complete trips, with payment processors for payouts, and with authorities when required by law. We do not sell your personal information.

Data Retention
We retain your information for as long as your account is active and as required by law. You may request deletion of your account and associated data at any time.

Your Rights
You have the right to access, correct, or delete your personal data. Contact us at privacy@spinr.ca for any privacy-related requests.

Contact Us
For privacy inquiries, contact: privacy@spinr.ca`;

const STATIC_TERMS_OF_SERVICE = `By using the Spinr driver application, you agree to these Terms of Service.

Independent Contractor Status
You are an independent contractor, not an employee of Spinr. You are responsible for your own taxes, insurance, and compliance with applicable laws.

Eligibility
You must be at least 21 years old, hold a valid driver's licence, maintain valid vehicle insurance, and have a vehicle that meets Spinr's requirements.

Conduct
You agree to treat riders with respect, maintain a safe and clean vehicle, follow all traffic laws, and complete trips as accepted. Discrimination or harassment of any kind is grounds for immediate deactivation.

Earnings and Payouts
Earnings are calculated based on completed trips minus the platform service fee. Payouts are processed via Stripe and transferred to your bank account within 2–3 business days.

Deactivation
Spinr may deactivate your account for violations of these Terms, low ratings, fraud, or safety incidents. You may appeal deactivation decisions through our support process.

Limitation of Liability
Spinr's liability to you is limited to the amounts paid to you in the 30 days prior to the claim. We are not liable for indirect, incidental, or consequential damages.

Governing Law
These Terms are governed by the laws of the Province of Ontario, Canada.

Contact Us
For questions: support@spinr.ca`;

export default function LegalScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const params = useLocalSearchParams();
    const type = params.type as 'tos' | 'privacy' | undefined;
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const scrollRef = useRef<ScrollView>(null);
    const privacyY = useRef(0);
    const tosY = useRef(0);

    const [privacyText, setPrivacyText] = useState('');
    const [tosText, setTosText] = useState('');
    const [loading, setLoading] = useState(true);

    // Declared before the `useEffect` below (react-hooks/immutability /
    // React Compiler flags referencing a function before its source-order
    // declaration) — same expression, same effect timing, no behavior change.
    const fetchLegalContent = async () => {
        try {
            // Public endpoint — no auth, no /api/v1 prefix (mounted at root).
            const response = await fetch(`${SpinrConfig.backendUrl}/settings/legal`);
            const data = await response.json();
            setPrivacyText(data.privacy_policy_text || STATIC_PRIVACY_POLICY);
            setTosText(data.terms_of_service_text || STATIC_TERMS_OF_SERVICE);
        } catch {
            setPrivacyText(STATIC_PRIVACY_POLICY);
            setTosText(STATIC_TERMS_OF_SERVICE);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchLegalContent();
    }, []);

    useEffect(() => {
        if (!loading && type && scrollRef.current) {
            const delay = setTimeout(() => {
                if (type === 'privacy') {
                    scrollRef.current?.scrollTo({ y: privacyY.current, animated: true });
                } else if (type === 'tos') {
                    scrollRef.current?.scrollTo({ y: tosY.current, animated: true });
                }
            }, 150);
            return () => clearTimeout(delay);
        }
    }, [loading, type]);

    return (
        <View style={styles.safeArea}>
            {/* Header */}
            <View style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
                    <Ionicons name="arrow-back" size={24} color={colors.text} />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Legal</Text>
                <View style={styles.headerRight} />
            </View>

            {loading ? (
                <View style={styles.loadingContainer}>
                    <ActivityIndicator size="large" color={colors.primary} />
                </View>
            ) : (
                <ScrollView
                    ref={scrollRef}
                    style={styles.container}
                    contentContainerStyle={styles.contentContainer}
                    showsVerticalScrollIndicator={false}
                >
                    {/* Privacy Policy Section */}
                    <View
                        onLayout={(e) => { privacyY.current = e.nativeEvent.layout.y; }}
                    >
                        <View style={styles.sectionHeader}>
                            <Ionicons name="shield-checkmark" size={20} color={colors.primary} />
                            <Text style={styles.sectionTitle}>Privacy Policy</Text>
                        </View>
                        <Text style={styles.textContent}>{privacyText}</Text>
                    </View>

                    <View style={styles.divider} />

                    {/* Terms of Service Section */}
                    <View
                        onLayout={(e) => { tosY.current = e.nativeEvent.layout.y; }}
                    >
                        <View style={styles.sectionHeader}>
                            <Ionicons name="document-text" size={20} color={colors.primary} />
                            <Text style={styles.sectionTitle}>Terms of Service</Text>
                        </View>
                        <Text style={styles.textContent}>{tosText}</Text>
                    </View>
                </ScrollView>
            )}
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        safeArea: {
            flex: 1,
            backgroundColor: colors.surface,
        },
        header: {
            flexDirection: 'row',
            alignItems: 'center',
            justifyContent: 'space-between',
            paddingHorizontal: 20,
            paddingVertical: 16,
            borderBottomWidth: 1,
            borderBottomColor: colors.border,
            backgroundColor: colors.surface,
        },
        backButton: {
            padding: 8,
            marginLeft: -8,
        },
        headerTitle: {
            fontSize: 18,
            fontWeight: '600',
            color: colors.text,
        },
        headerRight: {
            width: 40,
        },
        loadingContainer: {
            flex: 1,
            justifyContent: 'center',
            alignItems: 'center',
        },
        container: {
            flex: 1,
            backgroundColor: colors.surface,
        },
        contentContainer: {
            padding: 24,
            paddingBottom: 60,
        },
        sectionHeader: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            marginBottom: 16,
        },
        sectionTitle: {
            fontSize: 20,
            fontWeight: '700',
            color: colors.text,
        },
        textContent: {
            fontSize: 15,
            lineHeight: 24,
            color: colors.text,
        },
        divider: {
            height: 1,
            backgroundColor: colors.border,
            marginVertical: 32,
        },
    });
}
