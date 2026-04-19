import React, { useState, useEffect, useMemo } from 'react';
import {
    View,
    Text,
    StyleSheet,
    TouchableOpacity,
    FlatList,
    ActivityIndicator,
    Linking,
    RefreshControl,
} from 'react-native';
import CustomAlert, { AlertButton } from '@shared/components/CustomAlert';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import api from '@shared/api/client';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { ErrorBoundary } from '@shared/components/ErrorBoundary';

interface TaxDocument {
    id: string;
    type: string;
    tax_year: number;
    file_url: string;
    generated_at: string | null;
}

function TaxDocumentsScreen() {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { colors } = useTheme();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [documents, setDocuments] = useState<TaxDocument[]>([]);
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [downloadingId, setDownloadingId] = useState<string | null>(null);
    const [alert, setAlert] = useState<{
        visible: boolean; title: string; message?: string;
        variant: 'info' | 'success' | 'danger' | 'warning';
        buttons?: AlertButton[];
    }>({ visible: false, title: '', variant: 'info' });

    const showAlert = (title: string, message: string, variant: 'success' | 'danger' | 'warning' | 'info' = 'info', buttons?: AlertButton[]) => {
        setAlert({ visible: true, title, message, variant, buttons });
    };

    useEffect(() => {
        fetchDocuments();
    }, []);

    const fetchDocuments = async () => {
        try {
            const res = await api.get('/payouts/tax-documents');
            setDocuments(res.data.documents || res.data || []);
        } catch (err) {
            console.log('Error fetching tax documents:', err);
            showAlert('Error', 'Failed to load tax documents. Please try again.', 'danger');
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    };

    const onRefresh = () => {
        setRefreshing(true);
        fetchDocuments();
    };

    const handleDownload = async (doc: TaxDocument) => {
        setDownloadingId(doc.id);
        try {
            if (doc.file_url) {
                await Linking.openURL(doc.file_url);
            } else {
                showAlert('Unavailable', 'This document is not yet available for download.', 'warning');
            }
        } catch (err) {
            showAlert('Error', 'Could not open the document. Please try again.', 'danger');
        } finally {
            setDownloadingId(null);
        }
    };

    const formatDate = (dateStr: string | null) => {
        if (!dateStr) return '--';
        return new Date(dateStr).toLocaleDateString('en', {
            month: 'short', day: 'numeric', year: 'numeric',
        });
    };

    const getDocTypeLabel = (type: string) => {
        switch (type) {
            case 'T4A': return 'T4A Slip';
            case 'earnings_summary': return 'Earnings Summary';
            default: return type;
        }
    };

    const getDocTypeIcon = (type: string): any => {
        switch (type) {
            case 'T4A': return 'document-text';
            case 'earnings_summary': return 'bar-chart';
            default: return 'document';
        }
    };

    const renderDocumentItem = ({ item }: { item: TaxDocument }) => {
        const isDownloading = downloadingId === item.id;
        return (
            <View style={styles.docCard}>
                <View style={styles.docIcon}>
                    <Ionicons name={getDocTypeIcon(item.type)} size={24} color={colors.primary} />
                </View>
                <View style={styles.docInfo}>
                    <Text style={styles.docType}>{getDocTypeLabel(item.type)}</Text>
                    <Text style={styles.docYear}>Tax Year {item.tax_year}</Text>
                    {item.generated_at && (
                        <Text style={styles.docDate}>Generated {formatDate(item.generated_at)}</Text>
                    )}
                </View>
                <TouchableOpacity
                    style={[styles.downloadBtn, isDownloading && styles.downloadBtnDisabled]}
                    onPress={() => handleDownload(item)}
                    disabled={isDownloading}
                >
                    {isDownloading ? (
                        <ActivityIndicator size="small" color="#fff" />
                    ) : (
                        <Ionicons name="download-outline" size={18} color="#fff" />
                    )}
                </TouchableOpacity>
            </View>
        );
    };

    const renderEmpty = () => (
        <View style={styles.emptyState}>
            <Ionicons name="document-text-outline" size={64} color={colors.border} />
            <Text style={styles.emptyTitle}>No documents yet</Text>
            <Text style={styles.emptySub}>
                Tax documents will appear here once generated after your first tax year.
            </Text>
        </View>
    );

    return (
        <View style={styles.container}>
            <LinearGradient colors={[colors.surface, colors.background]} style={[styles.header, { paddingTop: insets.top + 12 }]}>
                <View style={styles.headerRow}>
                    <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                        <Ionicons name="arrow-back" size={22} color={colors.text} />
                    </TouchableOpacity>
                    <Text style={styles.headerTitle}>Tax Documents</Text>
                    <View style={{ width: 40 }} />
                </View>
            </LinearGradient>

            {loading ? (
                <ActivityIndicator size="large" color={colors.primary} style={{ marginTop: 60 }} />
            ) : (
                <FlatList
                    data={documents}
                    renderItem={renderDocumentItem}
                    keyExtractor={(item) => item.id}
                    contentContainerStyle={[
                        styles.listContent,
                        { paddingBottom: insets.bottom + 40 },
                    ]}
                    showsVerticalScrollIndicator={false}
                    removeClippedSubviews={true}
                    ListHeaderComponent={
                        <View style={styles.infoCard}>
                            <Ionicons name="information-circle" size={20} color={colors.primary} />
                            <Text style={styles.infoText}>
                                Download your T4A slips and earnings summaries for Canadian tax filing purposes.
                            </Text>
                        </View>
                    }
                    ListEmptyComponent={renderEmpty}
                    refreshControl={
                        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
                    }
                />
            )}

            <CustomAlert
                visible={alert.visible}
                title={alert.title}
                message={alert.message}
                variant={alert.variant}
                buttons={alert.buttons || [{ text: 'OK', style: 'default' }]}
                onClose={() => setAlert(a => ({ ...a, visible: false }))}
            />
        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        header: {
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
            backgroundColor: colors.surfaceLight,
            justifyContent: 'center',
            alignItems: 'center',
        },
        headerTitle: { color: colors.text, fontSize: 20, fontWeight: '700' },

        listContent: {
            paddingHorizontal: 16,
            paddingTop: 16,
        },

        infoCard: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            backgroundColor: colors.surface,
            borderRadius: 12,
            padding: 14,
            marginBottom: 16,
            gap: 10,
            borderWidth: 1,
            borderColor: colors.border,
        },
        infoText: {
            flex: 1,
            color: colors.textDim,
            fontSize: 13,
            lineHeight: 18,
        },

        docCard: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: colors.surface,
            borderRadius: 16,
            padding: 16,
            marginBottom: 12,
            borderWidth: 1,
            borderColor: colors.border,
        },
        docIcon: {
            width: 48,
            height: 48,
            borderRadius: 12,
            backgroundColor: `${colors.primary}15`,
            justifyContent: 'center',
            alignItems: 'center',
            marginRight: 14,
        },
        docInfo: { flex: 1 },
        docType: {
            color: colors.text,
            fontSize: 16,
            fontWeight: '700',
            marginBottom: 2,
        },
        docYear: {
            color: colors.textDim,
            fontSize: 13,
            fontWeight: '600',
        },
        docDate: {
            color: colors.textDim,
            fontSize: 12,
            marginTop: 2,
        },

        downloadBtn: {
            backgroundColor: colors.primary,
            width: 40,
            height: 40,
            borderRadius: 20,
            justifyContent: 'center',
            alignItems: 'center',
        },
        downloadBtnDisabled: {
            opacity: 0.6,
        },

        emptyState: {
            alignItems: 'center',
            paddingVertical: 60,
            paddingHorizontal: 30,
        },
        emptyTitle: {
            color: colors.text,
            fontSize: 18,
            fontWeight: '600',
            marginTop: 16,
        },
        emptySub: {
            color: colors.textDim,
            fontSize: 14,
            marginTop: 6,
            textAlign: 'center',
            lineHeight: 20,
        },
    });
}

export default function TaxDocumentsScreenWithBoundary() {
  return (
    <ErrorBoundary>
      <TaxDocumentsScreen />
    </ErrorBoundary>
  );
}
