import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { View, StyleSheet, TouchableOpacity, ScrollView, ActivityIndicator, Image, Modal, StatusBar, Alert } from 'react-native';
import { Text } from '@shared/components/Text';
import { showToast } from '../hooks/useToast';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useFocusEffect } from "expo-router/react-navigation";
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import api, { getApiErrorMessage } from '@shared/api/client';
import { uploadFile, resolveUploadMimeType } from '@shared/api/upload';
import { useAuthStore } from '@shared/store/authStore';
// Keep the default import: many test files jest.mock(
// '@shared/config/spinr.config', () => ({ default: {...} })) without a
// matching named 'SpinrConfig' export, so switching to a named import
// breaks those mocks (confirmed in rider-app's utils/aiChat.ts).
// eslint-disable-next-line import/no-named-as-default
import SpinrConfig from '@shared/config/spinr.config';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { Button } from '@shared/components/Button';
import { useLogRocketPrivacyScreen } from '@shared/hooks/useLogRocketPrivacyScreen';
import { SPACING, FONT } from '@shared/utils/responsive';
import { ScreenHeader } from '../components/ScreenHeader';


// Module-level (not component-scope) so react-hooks/purity doesn't treat this
// as an impure call "during render" — it's only ever invoked from the
// pickImage event handler, well after mount, never during render itself.
function genFallbackFileName(): string {
    return `photo_${Date.now()}.jpg`;
}

interface Requirement {
    id: string;
    name: string;
    description: string;
    is_mandatory: boolean;
    requires_back_side: boolean;
}

interface DriverDocument {
    id: string;
    requirement_id: string | null;
    requirement_key?: string | null;
    document_type?: string | null;
    document_url: string;
    status: 'pending' | 'approved' | 'rejected';
    rejection_reason?: string;
    side?: 'front' | 'back';
}

export default function DocumentsScreen() {
    const insets = useSafeAreaInsets();
    const { fetchDriverProfile, driver } = useAuthStore();
    const { colors } = useTheme();
    // #1231 finding 17: ID/vehicle document photos must never enter session replay.
    useLogRocketPrivacyScreen();
    const styles = useMemo(() => createStyles(colors), [colors]);
    const [loading, setLoading] = useState(true);
    const [requirements, setRequirements] = useState<Requirement[]>([]);
    const [documents, setDocuments] = useState<DriverDocument[]>([]);
    const [uploading, setUploading] = useState<string | null>(null);
    const [previewUrl, setPreviewUrl] = useState<string | null>(null);

    const loadData = async () => {
        try {
            const [reqRes, docRes] = await Promise.all([
                api.get<Requirement[]>('/drivers/requirements'),
                api.get<DriverDocument[]>('/drivers/documents')
            ]);
            setRequirements(reqRes.data as Requirement[]);
            setDocuments(docRes.data as DriverDocument[]);
        } catch (err: any) {
            showToast('error', 'Load Failed', getApiErrorMessage(err, 'Could not load your documents. Please try again.'));
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        // Mount-only fetch; loadData sets state after its own await, not
        // synchronously at the top of the effect. Empty deps, runs once.
        // eslint-disable-next-line react-hooks/set-state-in-effect
        loadData();
    }, []);

    // Re-fetch whenever the screen comes into focus (e.g. returning from admin review)
    useFocusEffect(
        useCallback(() => {
            loadData();
        }, [])
    );

    const processUpload = async (uri: string, name: string, mimeType: string, reqId: string, side: 'front' | 'back') => {
        try {
            setUploading(`${reqId}-${side}`);

            // 1. Upload via the shared helper, which posts over XMLHttpRequest.
            //
            // This screen used to inline its own fetch() call. Two reasons it
            // no longer does: the duplicate drifted from shared/api/upload.ts,
            // and more importantly Expo SDK 54+ swaps global fetch for its
            // WinterCG implementation, which rejects React Native's
            // { uri, name, type } FormData part with "Unsupported FormDataPart
            // implementation" before the request is ever sent. See the comment
            // on postMultipart for the full mechanism.
            const fileUrl = await uploadFile(uri, name, mimeType);
            if (!fileUrl) {
                throw new Error('Upload succeeded but server did not return a file URL.');
            }

            // 2. Link to driver (axios is fine for plain JSON).
            // Use the requirement name as document_type (not the MIME type) so the
            // admin dashboard can match uploaded docs to service-area requirements.
            const matchedReq = requirements.find(r => r.id === reqId);
            await api.post('/drivers/documents', {
                requirement_id: reqId,
                document_url: fileUrl,
                side,
                document_type: matchedReq?.name || mimeType,
            });

            // 3. Refresh UI
            await loadData();
            await fetchDriverProfile();

            showToast('success', 'Uploaded', 'Document submitted for review.');
        } catch (err: any) {
            showToast('error', 'Upload Failed', getApiErrorMessage(err, 'Could not upload your document. Please try again.'));
        } finally {
            setUploading(null);
        }
    };

    const pickImage = async (reqId: string, side: 'front' | 'back', useCamera: boolean) => {
        try {
            if (useCamera) {
                const { status } = await ImagePicker.requestCameraPermissionsAsync();
                if (status !== 'granted') {
                    showToast('warning', 'Permission needed', 'Camera permission is required to take photos.');
                    return;
                }
            } else {
                const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
                if (status !== 'granted') {
                    showToast('warning', 'Permission needed', 'Gallery permission is required to upload photos.');
                    return;
                }
            }

            const result = useCamera
                ? await ImagePicker.launchCameraAsync({
                    mediaTypes: ['images'],
                    quality: 0.8,
                    allowsEditing: true,
                })
                : await ImagePicker.launchImageLibraryAsync({
                    mediaTypes: ['images'],
                    quality: 0.8,
                    allowsEditing: false,
                });

            if (!result.canceled && result.assets && result.assets.length > 0) {
                const asset = result.assets[0];
                const name = asset.fileName || genFallbackFileName();
                // asset.type from expo-image-picker is 'image'|'video', not a MIME type.
                // Derive the real MIME from the file extension so the backend magic-byte
                // check doesn't reject a PNG declared as image/jpeg.
                const mimeType = resolveUploadMimeType(name || asset.uri, asset.type);

                await processUpload(asset.uri, name, mimeType, reqId, side);
            }
        } catch {
            showToast('error', 'Error', 'Failed to pick image');
        }
    };

    const pickFile = async (reqId: string, side: 'front' | 'back') => {
        try {
            const result = await DocumentPicker.getDocumentAsync({
                type: ['image/*', 'application/pdf'],
                copyToCacheDirectory: true,
            });

            if (result.canceled) return;

            const asset = result.assets[0];
            const mimeType = resolveUploadMimeType(asset.name || asset.uri, asset.mimeType);
            await processUpload(asset.uri, asset.name, mimeType, reqId, side);

        } catch {
            // processUpload surfaces its own API errors and doesn't rethrow, so
            // this only sees DocumentPicker failures — a raw native err.message
            // is meaningless to the driver; match pickImage's clean generic.
            showToast('error', 'Upload Failed', 'Could not open that file. Please try again.');
        }
    };

    const handleUpload = async (reqId: string, side: 'front' | 'back') => {
        Alert.alert('Upload Document', 'Choose a source', [
            { text: 'Camera', onPress: () => pickImage(reqId, side, true) },
            { text: 'Gallery', onPress: () => pickImage(reqId, side, false) },
            { text: 'File', onPress: () => pickFile(reqId, side) },
            { text: 'Cancel', style: 'cancel' },
        ]);
    };

    const getDocStatus = (reqId: string, side: 'front' | 'back' = 'front') => {
        const req = requirements.find(r => r.id === reqId);
        // Match strategies in order: requirement_key (slug, authoritative when
        // migration 28 is applied) → requirement_id (UUID or legacy slug) →
        // document_type name (fallback for rows missing requirement_key).
        const doc = documents.find(d => {
            const matches =
                (d.requirement_key && d.requirement_key === reqId) ||
                (d.requirement_id && d.requirement_id === reqId) ||
                (!d.requirement_id && !d.requirement_key && req && d.document_type === req.name);
            return matches && (d.side === side || !d.side);
        });
        if (!doc) return 'missing';
        return doc;
    };

    const renderStatusBadge = (status: string, reason?: string) => {
        if (status === 'approved') return <View style={[styles.badge, { backgroundColor: colors.success }]}><Text style={styles.badgeText}>Verified</Text></View>;
        if (status === 'rejected') return (
            <View>
                <View style={[styles.badge, { backgroundColor: colors.error }]}><Text style={styles.badgeText}>Rejected</Text></View>
                {reason && <Text style={styles.rejectReason}>{reason}</Text>}
            </View>
        );
        if (status === 'pending') return <View style={[styles.badge, { backgroundColor: colors.warning }]}><Text style={styles.badgeText}>Pending</Text></View>;
        return <View style={[styles.badge, { backgroundColor: '#F3F4F6' }]}><Text style={[styles.badgeText, { color: colors.textDim }]}>Missing</Text></View>;
    };

    // Derive expiry status from a date string on the document record itself.
    // Requirements are service-area-specific and admin-named, so we never
    // keyword-match requirement names to driver-profile fields.
    const getExpiryInfo = (expiryDateStr: string | null | undefined) => {
        if (!expiryDateStr) return { status: 'none', label: '', date: '', expiresIn: null };
        const expiryDate = new Date(expiryDateStr);
        const now = new Date();
        const daysLeft = Math.ceil((expiryDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24));
        const isExpired = daysLeft < 0;
        const isExpiringSoon = !isExpired && daysLeft < 30;
        return {
            status: isExpired ? 'expired' : isExpiringSoon ? 'expiring_soon' : 'valid',
            label: isExpired ? 'EXPIRED' : isExpiringSoon ? `Expires in ${daysLeft} days` : 'Valid',
            date: expiryDate.toLocaleDateString(),
            expiresIn: daysLeft,
        };
    };

    if (loading) {
        return (
            <View style={[styles.container, styles.center]}>
                <ActivityIndicator size="large" color={colors.primary} />
            </View>
        );
    }

    // legacy_import_metadata reaches /drivers/me unfiltered today (not in
    // _STRIP_FROM_SELF_RESPONSE — backend/routes/drivers/profile.py) even
    // though its own column comment (migration 221) scopes it to
    // "admin/compliance use only" — an existing overexposure this fix
    // consumes rather than causes. Kept to a presence check only; nothing
    // from inside the object is ever rendered, to stay within that intent
    // as closely as this UI-only fix can.
    const legacyMeta = driver?.legacy_import_metadata as Record<string, unknown> | undefined;
    const isLegacyImportedDriver = !!legacyMeta && Object.keys(legacyMeta).length > 0;
    // A real migration gap affects every requirement equally (the old app's
    // document IMAGES were never part of the export — only driver rows were)
    // rather than a genuine one-off missing upload, so this only shows for a
    // driver with literally nothing on file yet.
    const showLegacyDocsGapNotice = isLegacyImportedDriver && documents.length === 0;

    return (
        <View style={styles.container}>
            <StatusBar barStyle="dark-content" />
            <ScreenHeader title="Documents" />

            <ScrollView contentContainerStyle={[styles.content, { paddingBottom: insets.bottom + 40 }]}>
                <View style={styles.infoBox}>
                    <Ionicons name="information-circle-outline" size={20} color={colors.primary} style={{ marginRight: 8 }} />
                    <Text style={styles.infoText}>
                        Keep your documents up to date to maintain your driver status.
                    </Text>
                </View>

                {/* A legacy-imported driver whose old-app document images never made
                    it into this export sees the exact same "Missing"/"UPLOAD
                    REQUIRED" copy below as someone who genuinely never uploaded
                    anything, even though they're already approved and driving —
                    this banner adds context without changing the per-requirement
                    status logic itself. */}
                {showLegacyDocsGapNotice && (
                    <View style={styles.legacyInfoBox}>
                        <Ionicons name="time-outline" size={20} color={colors.textDim} style={{ marginRight: 8 }} />
                        <Text style={styles.legacyInfoText}>
                            Your documents from your previous Spinr account weren&apos;t part of
                            this transfer — that&apos;s a data-migration gap, not a sign
                            anything is missing from your file. You&apos;re still an approved,
                            active driver. Re-upload below whenever it&apos;s convenient, or
                            contact support with any questions.
                        </Text>
                    </View>
                )}

                {requirements.map((req) => {
                    // Get the overall document upload status
                    const frontDoc = getDocStatus(req.id, 'front');
                    const frontStatus = frontDoc === 'missing' ? 'missing' : frontDoc.status;

                    // Expiry comes from the document record (set by admin on approval).
                    // No keyword matching on requirement names — requirements are
                    // service-area-specific and admin-defined.
                    const expiryInfo = frontDoc !== 'missing'
                        ? getExpiryInfo((frontDoc as any).expiry_date)
                        : null;

                    // Determine card border color based on overall state
                    const cardBorderColor = frontStatus === 'approved' && expiryInfo?.status === 'valid'
                        ? colors.success
                        : frontStatus === 'approved' && expiryInfo?.status === 'expired'
                            ? colors.error
                            : frontStatus === 'rejected'
                                ? colors.error
                                : frontStatus === 'pending'
                                    ? colors.warning
                                    : colors.border;

                    return (
                        <View key={req.id} style={[styles.card, { borderColor: cardBorderColor }]}>
                            <View style={styles.cardHeader}>
                                <View style={{ flex: 1 }}>
                                    <Text style={styles.cardTitle}>{req.name}</Text>
                                </View>
                                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                                    {req.is_mandatory && <Text style={styles.mandatory}>Required</Text>}
                                    {/* Overall status icon */}
                                    {frontStatus === 'approved' && expiryInfo?.status === 'valid' && (
                                        <Ionicons name="checkmark-circle" size={20} color={colors.success} />
                                    )}
                                    {frontStatus === 'approved' && expiryInfo?.status === 'expiring_soon' && (
                                        <Ionicons name="alert-circle" size={20} color={colors.warning} />
                                    )}
                                    {frontStatus === 'approved' && expiryInfo?.status === 'expired' && (
                                        <Ionicons name="warning" size={20} color={colors.error} />
                                    )}
                                    {frontStatus === 'pending' && (
                                        <Ionicons name="time-outline" size={20} color={colors.warning} />
                                    )}
                                    {frontStatus === 'rejected' && (
                                        <Ionicons name="close-circle" size={20} color={colors.error} />
                                    )}
                                    {frontStatus === 'missing' && (
                                        <Ionicons name="document-outline" size={20} color={colors.textDim} />
                                    )}
                                </View>
                            </View>

                            <Text style={styles.cardDesc}>{req.description}</Text>

                            {/* Expiry & Verification Status Row */}
                            <View style={styles.statusRow}>
                                {/* Verification status badge */}
                                {(() => {
                                    if (frontStatus === 'approved') return (
                                        <View style={[styles.statusBadge, { backgroundColor: colors.successBg }]}>
                                            <Ionicons name="checkmark-circle" size={12} color={colors.success} />
                                            <Text style={[styles.statusBadgeText, { color: colors.success }]}>Verified</Text>
                                        </View>
                                    );
                                    if (frontStatus === 'pending') return (
                                        <View style={[styles.statusBadge, { backgroundColor: '#FFFBEB' }]}>
                                            <Ionicons name="time-outline" size={12} color={colors.warning} />
                                            <Text style={[styles.statusBadgeText, { color: colors.warning }]}>Pending Review</Text>
                                        </View>
                                    );
                                    if (frontStatus === 'rejected') return (
                                        <View style={[styles.statusBadge, { backgroundColor: colors.dangerBg }]}>
                                            <Ionicons name="close-circle" size={12} color={colors.error} />
                                            <Text style={[styles.statusBadgeText, { color: colors.error }]}>Rejected</Text>
                                        </View>
                                    );
                                    return (
                                        <View style={[styles.statusBadge, { backgroundColor: '#F3F4F6' }]}>
                                            <Ionicons name="document-outline" size={12} color={colors.textDim} />
                                            <Text style={[styles.statusBadgeText, { color: colors.textDim }]}>Not Submitted</Text>
                                        </View>
                                    );
                                })()}

                                {/* Expiry badge — only renders when document record has an expiry_date set */}
                                {expiryInfo && expiryInfo.status !== 'none' && (
                                    <View style={[styles.statusBadge, {
                                        backgroundColor: expiryInfo.status === 'expired' ? colors.dangerBg
                                            : expiryInfo.status === 'expiring_soon' ? '#FFFBEB'
                                            : colors.successBg,
                                    }]}>
                                        <Ionicons name="calendar-outline" size={12} color={
                                            expiryInfo.status === 'expired' ? colors.error
                                                : expiryInfo.status === 'expiring_soon' ? colors.warning
                                                : colors.success
                                        } />
                                        <Text style={[styles.statusBadgeText, {
                                            color: expiryInfo.status === 'expired' ? colors.error
                                                : expiryInfo.status === 'expiring_soon' ? colors.warning
                                                : colors.success,
                                        }]}>
                                            {expiryInfo.label} • {expiryInfo.date}
                                        </Text>
                                    </View>
                                )}
                            </View>

                            {/* Rejection reason + re-upload nudge */}
                            {frontStatus === 'rejected' && frontDoc !== 'missing' && (
                                <View style={styles.rejectionBlock}>
                                    {frontDoc.rejection_reason && (
                                        <View style={styles.rejectionRow}>
                                            <Ionicons name="alert-circle" size={14} color={colors.error} />
                                            <Text style={styles.rejectReason}>{frontDoc.rejection_reason}</Text>
                                        </View>
                                    )}
                                    {/* UX3 (ACTION_ITEMS.md): migrated onto the shared Button
                                        (variant="primary" size="sm" icon="cloud-upload-outline") —
                                        borderRadius:10/fontSize:13/fontWeight:600 already matched
                                        this button's own reuploadBtn/reuploadBtnText styles, so
                                        the swap is visually a no-op. */}
                                    <Button
                                        variant="primary"
                                        size="sm"
                                        icon="cloud-upload-outline"
                                        style={styles.reuploadBtn}
                                        onPress={() => handleUpload(req.id, 'front')}
                                    >
                                        Re-upload Document
                                    </Button>
                                </View>
                            )}

                            {/* Front Side */}
                            <View style={styles.uploadRow}>
                                <View style={{ flex: 1, marginRight: 10 }}>
                                    <Text style={styles.sideLabel}>Front Side / Main Document</Text>
                                    {(() => {
                                        const doc = getDocStatus(req.id, 'front');
                                        if (doc === 'missing') return renderStatusBadge('missing');
                                        return (
                                            <View>
                                                {renderStatusBadge(doc.status, doc.rejection_reason)}
                                                {doc.document_url && (
                                                    <TouchableOpacity
                                                        style={styles.previewContainer}
                                                        onPress={() => setPreviewUrl(
                                                            doc.document_url.startsWith('http')
                                                                ? doc.document_url
                                                                : `${SpinrConfig.backendUrl}${doc.document_url}`
                                                        )}
                                                    >
                                                        <Image
                                                            source={{ uri: doc.document_url.startsWith('http') ? doc.document_url : `${SpinrConfig.backendUrl}${doc.document_url}` }}
                                                            style={styles.docPreview}
                                                            resizeMode="cover"
                                                        />
                                                    </TouchableOpacity>
                                                )}
                                            </View>
                                        );
                                    })()}
                                </View>
                                {/* Not migrated (UX3, ACTION_ITEMS.md): a compact 44px square
                                    icon-over-label tile, not a horizontal text CTA — Button's
                                    children model assumes a single-line label (plus an optional
                                    leading icon), not a 2-line icon-over-text stack. Genuinely
                                    bespoke; left as its own TouchableOpacity. */}
                                <TouchableOpacity
                                    style={styles.uploadBtn}
                                    onPress={() => handleUpload(req.id, 'front')}
                                    disabled={!!uploading}
                                >
                                    {uploading === `${req.id}-front` ? (
                                        <ActivityIndicator color={colors.primary} />
                                    ) : (
                                        <View style={styles.uploadIconContainer}>
                                            <Ionicons name="cloud-upload-outline" size={20} color={colors.primary} />
                                            <Text style={{ fontSize: 10, color: colors.primary, fontWeight: '600' }}>UPLOAD</Text>
                                        </View>
                                    )}
                                </TouchableOpacity>
                            </View>

                            {/* Back Side */}
                            {req.requires_back_side && (
                                <View style={[styles.uploadRow, { marginTop: 15, borderTopWidth: 1, borderTopColor: colors.border, paddingTop: 15 }]}>
                                    <View style={{ flex: 1, marginRight: 10 }}>
                                        <Text style={styles.sideLabel}>Back Side</Text>
                                        {(() => {
                                            const doc = getDocStatus(req.id, 'back');
                                            if (doc === 'missing') return renderStatusBadge('missing');
                                            return (
                                                <View>
                                                    {renderStatusBadge(doc.status, doc.rejection_reason)}
                                                    {doc.document_url && (
                                                        <TouchableOpacity
                                                            style={styles.previewContainer}
                                                            onPress={() => setPreviewUrl(
                                                                doc.document_url.startsWith('http')
                                                                    ? doc.document_url
                                                                    : `${SpinrConfig.backendUrl}${doc.document_url}`
                                                            )}
                                                        >
                                                            <Image
                                                                source={{ uri: doc.document_url.startsWith('http') ? doc.document_url : `${SpinrConfig.backendUrl}${doc.document_url}` }}
                                                                style={styles.docPreview}
                                                                resizeMode="cover"
                                                            />
                                                        </TouchableOpacity>
                                                    )}
                                                </View>
                                            );
                                        })()}
                                    </View>
                                    <TouchableOpacity
                                        style={styles.uploadBtn}
                                        onPress={() => handleUpload(req.id, 'back')}
                                        disabled={!!uploading}
                                    >
                                        {uploading === `${req.id}-back` ? (
                                            <ActivityIndicator color={colors.primary} />
                                        ) : (
                                            <View style={styles.uploadIconContainer}>
                                                <Ionicons name="cloud-upload-outline" size={20} color={colors.primary} />
                                                <Text style={{ fontSize: 10, color: colors.primary, fontWeight: '600' }}>UPLOAD</Text>
                                            </View>
                                        )}
                                    </TouchableOpacity>
                                </View>
                            )}
                        </View>
                    );
                })}
            </ScrollView>

            {/* Full-screen document preview modal */}
            <Modal
                visible={!!previewUrl}
                transparent
                animationType="fade"
                statusBarTranslucent
                onRequestClose={() => setPreviewUrl(null)}
            >
                <View style={styles.previewModal}>
                    <TouchableOpacity
                        style={styles.previewModalClose}
                        onPress={() => setPreviewUrl(null)}
                        hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
                    >
                        <Ionicons name="close-circle" size={36} color="#fff" />
                    </TouchableOpacity>
                    {previewUrl && (
                        <Image
                            source={{ uri: previewUrl }}
                            style={styles.previewModalImage}
                            resizeMode="contain"
                        />
                    )}
                </View>
            </Modal>

        </View>
    );
}

function createStyles(colors: ThemeColors) {
    return StyleSheet.create({
        container: { flex: 1, backgroundColor: colors.background },
        center: { justifyContent: 'center', alignItems: 'center' },
        content: { padding: 20 },
        infoBox: {
            flexDirection: 'row',
            alignItems: 'center',
            backgroundColor: '#FFF5F5', // Light red tint
            padding: 15,
            borderRadius: 12,
            marginBottom: 20,
            borderWidth: 1,
            borderColor: '#FFE4E6',
        },
        infoText: { color: colors.primaryDark, fontSize: FONT.bodySm, lineHeight: 20, flex: 1 },
        legacyInfoBox: {
            flexDirection: 'row',
            alignItems: 'flex-start',
            backgroundColor: colors.surfaceLight,
            padding: 15,
            borderRadius: 12,
            marginBottom: 20,
            borderWidth: 1,
            borderColor: colors.border,
        },
        legacyInfoText: { color: colors.textDim, fontSize: FONT.bodySm, lineHeight: 20, flex: 1 },
        card: {
            backgroundColor: colors.surface,
            borderRadius: 12,
            padding: SPACING.md,
            marginBottom: SPACING.md,
            borderWidth: 1,
            borderColor: colors.border,
            shadowColor: '#000',
            shadowOffset: { width: 0, height: 1 },
            shadowOpacity: 0.05,
            shadowRadius: 2,
            elevation: 2,
        },
        cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
        cardTitle: { fontSize: FONT.bodyLg, fontWeight: '600', color: colors.text },
        mandatory: { color: colors.error, fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
        cardDesc: { color: colors.textSecondary, fontSize: FONT.bodySm, marginBottom: 15 },
        uploadRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
        sideLabel: { color: colors.textSecondary, fontSize: FONT.bodySm, marginBottom: SPACING.xs, fontWeight: '500' },
        badge: { paddingHorizontal: SPACING.sm, paddingVertical: 2, borderRadius: 4, alignSelf: 'flex-start' },
        badgeText: { color: '#fff', fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
        rejectReason: { color: colors.error, fontSize: FONT.label, marginTop: 2, flex: 1 },
        statusRow: {
            flexDirection: 'row',
            flexWrap: 'wrap',
            gap: 8,
            marginBottom: 14,
        },
        statusBadge: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 4,
            paddingHorizontal: 10,
            paddingVertical: 5,
            borderRadius: 8,
        },
        statusBadgeText: {
            fontSize: FONT.label,
            fontWeight: '600',
        },
        rejectionBlock: {
            marginBottom: 14,
        },
        rejectionRow: {
            flexDirection: 'row',
            alignItems: 'center',
            gap: 6,
            backgroundColor: colors.dangerBg,
            paddingHorizontal: 10,
            paddingVertical: 6,
            borderRadius: 8,
        },
        // Fill/radius/padding/text now come from the shared Button
        // (variant="primary" size="sm" icon="cloud-upload-outline") — this
        // only supplies the spacing above it.
        reuploadBtn: {
            marginTop: SPACING.sm,
        },
        uploadBtn: {
            padding: SPACING.sm,
            borderRadius: 8,
            backgroundColor: '#FFF5F5',
            borderWidth: 1,
            borderColor: '#FECACA',
        },
        uploadIconContainer: {
            alignItems: 'center',
            justifyContent: 'center',
            width: 44,
            gap: 2,
        },
        previewContainer: {
            marginTop: SPACING.sm,
            borderRadius: 8,
            overflow: 'hidden',
            width: 100,
            height: 60,
            backgroundColor: colors.surfaceLight,
            borderWidth: 1,
            borderColor: colors.border,
        },
        docPreview: {
            width: '100%',
            height: '100%',
        },
        previewModal: {
            flex: 1,
            backgroundColor: 'rgba(0,0,0,0.92)',
            justifyContent: 'center',
            alignItems: 'center',
        },
        previewModalClose: {
            position: 'absolute',
            top: 52,
            right: 20,
            zIndex: 10,
        },
        previewModalImage: {
            width: '100%',
            height: '80%',
        },
    });
}
