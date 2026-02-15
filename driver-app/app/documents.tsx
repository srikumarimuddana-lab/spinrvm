import React, { useState, useEffect } from 'react';
import { View, Text, StyleSheet, TouchableOpacity, ScrollView, Alert, ActivityIndicator, Image, Platform, StatusBar } from 'react-native';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as DocumentPicker from 'expo-document-picker';
import api from '@shared/api/client';
import { useAuthStore } from '@shared/store/authStore';
import SpinrConfig from '@shared/config/spinr.config';

const THEME = SpinrConfig.theme.colors;

interface Requirement {
    id: string;
    name: string;
    description: string;
    is_mandatory: boolean;
    requires_back_side: boolean;
}

interface DriverDocument {
    id: string;
    requirement_id: string;
    document_url: string;
    status: 'pending' | 'approved' | 'rejected';
    rejection_reason?: string;
    side?: 'front' | 'back';
}

export default function DocumentsScreen() {
    const router = useRouter();
    // Force rebuild
    const { driver, fetchDriverProfile } = useAuthStore();
    const [loading, setLoading] = useState(true);
    const [requirements, setRequirements] = useState<Requirement[]>([]);
    const [documents, setDocuments] = useState<DriverDocument[]>([]);
    const [uploading, setUploading] = useState<string | null>(null);

    const loadData = async () => {
        try {
            const [reqRes, docRes] = await Promise.all([
                api.get('/drivers/requirements'),
                api.get('/drivers/documents')
            ]);
            setRequirements(reqRes.data);
            setDocuments(docRes.data);
        } catch (err: any) {
            console.error("Documents load error:", err);
            if (err.response) {
                console.error("Error status:", err.response.status);
                console.error("Error data:", err.response.data);
            }
            Alert.alert("Error", `Failed to load documents: ${err.message}`);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
    }, []);

    const processUpload = async (uri: string, name: string, mimeType: string, reqId: string, side: 'front' | 'back') => {
        try {
            setUploading(`${reqId}-${side}`);

            // 1. Upload file
            const formData = new FormData();
            formData.append('file', {
                uri: uri,
                name: name,
                type: mimeType,
            } as any);

            const uploadRes = await api.post('/upload', formData, {
                headers: { 'Content-Type': 'multipart/form-data' },
            });

            const fileUrl = uploadRes.data.url;

            // 2. Link to driver
            await api.post('/drivers/documents', {
                requirement_id: reqId,
                document_url: fileUrl,
                side: side,
                document_type: mimeType,
            });

            // 3. Refresh
            await loadData();
            await fetchDriverProfile();

            Alert.alert("Uploaded", "Document submitted for review.");

        } catch (err: any) {
            Alert.alert("Upload Failed", err.message);
        } finally {
            setUploading(null);
        }
    };

    const pickImage = async (reqId: string, side: 'front' | 'back', useCamera: boolean) => {
        try {
            if (useCamera) {
                const { status } = await ImagePicker.requestCameraPermissionsAsync();
                if (status !== 'granted') {
                    Alert.alert('Permission needed', 'Camera permission is required to take photos.');
                    return;
                }
            } else {
                const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
                if (status !== 'granted') {
                    Alert.alert('Permission needed', 'Gallery permission is required to upload photos.');
                    return;
                }
            }

            const result = useCamera
                ? await ImagePicker.launchCameraAsync({
                    mediaTypes: ImagePicker.MediaTypeOptions.Images,
                    quality: 0.8,
                    allowsEditing: true,
                })
                : await ImagePicker.launchImageLibraryAsync({
                    mediaTypes: ImagePicker.MediaTypeOptions.Images,
                    quality: 0.8,
                    allowsEditing: false,
                });

            if (!result.canceled && result.assets && result.assets.length > 0) {
                const asset = result.assets[0];
                const name = asset.fileName || `photo_${Date.now()}.jpg`;
                const mimeType = asset.type === 'image' || !asset.type ? 'image/jpeg' : asset.type;

                await processUpload(asset.uri, name, mimeType, reqId, side);
            }
        } catch (e) {
            Alert.alert('Error', 'Failed to pick image');
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
            await processUpload(asset.uri, asset.name, asset.mimeType || 'image/jpeg', reqId, side);

        } catch (err: any) {
            Alert.alert("Upload Failed", err.message);
        }
    };

    const handleUpload = async (reqId: string, side: 'front' | 'back') => {
        if (Platform.OS === 'ios') {
            Alert.alert(
                'Upload Document',
                'Choose a source',
                [
                    { text: 'Camera', onPress: () => pickImage(reqId, side, true) },
                    { text: 'Gallery', onPress: () => pickImage(reqId, side, false) },
                    { text: 'File', onPress: () => pickFile(reqId, side) },
                    { text: 'Cancel', style: 'cancel' }
                ]
            );
        } else {
            Alert.alert(
                'Upload Document',
                'Choose a source',
                [
                    { text: 'Camera', onPress: () => pickImage(reqId, side, true) },
                    { text: 'Gallery', onPress: () => pickImage(reqId, side, false) },
                    { text: 'File / Cancel', onPress: () => pickFile(reqId, side) },
                ],
                { cancelable: true }
            );
        }
    };

    const getDocStatus = (reqId: string, side: 'front' | 'back' = 'front') => {
        const doc = documents.find(d => d.requirement_id === reqId && (d.side === side || !d.side));
        if (!doc) return 'missing';
        return doc;
    };

    const renderStatusBadge = (status: string, reason?: string) => {
        if (status === 'approved') return <View style={[styles.badge, { backgroundColor: THEME.success }]}><Text style={styles.badgeText}>Verified</Text></View>;
        if (status === 'rejected') return (
            <View>
                <View style={[styles.badge, { backgroundColor: THEME.error }]}><Text style={styles.badgeText}>Rejected</Text></View>
                {reason && <Text style={styles.rejectReason}>{reason}</Text>}
            </View>
        );
        if (status === 'pending') return <View style={[styles.badge, { backgroundColor: THEME.warning }]}><Text style={styles.badgeText}>Pending</Text></View>;
        return <View style={[styles.badge, { backgroundColor: '#F3F4F6' }]}><Text style={[styles.badgeText, { color: '#666' }]}>Missing</Text></View>;
    };

    if (loading) {
        return (
            <View style={[styles.container, styles.center]}>
                <ActivityIndicator size="large" color={THEME.primary} />
            </View>
        );
    }

    return (
        <View style={styles.container}>
            <StatusBar barStyle="dark-content" />
            <View style={styles.header}>
                <TouchableOpacity onPress={() => router.back()} style={styles.backBtn}>
                    <Ionicons name="arrow-back" size={24} color="#000" />
                </TouchableOpacity>
                <Text style={styles.headerTitle}>Documents</Text>
                <View style={{ width: 24 }} />
            </View>

            <ScrollView contentContainerStyle={styles.content}>
                <View style={styles.infoBox}>
                    <Ionicons name="information-circle-outline" size={20} color={THEME.primary} style={{ marginRight: 8 }} />
                    <Text style={styles.infoText}>
                        Keep your documents up to date to maintain your driver status.
                    </Text>
                </View>

                {requirements.map((req) => (
                    <View key={req.id} style={styles.card}>
                        <View style={styles.cardHeader}>
                            <Text style={styles.cardTitle}>{req.name}</Text>
                            {req.is_mandatory && <Text style={styles.mandatory}>Required</Text>}
                        </View>
                        <Text style={styles.cardDesc}>{req.description}</Text>

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
                                                    onPress={() => {
                                                        // TODO: Full screen preview
                                                    }}
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
                                onPress={() => handleUpload(req.id, 'front')}
                                disabled={!!uploading}
                            >
                                {uploading === `${req.id}-front` ? (
                                    <ActivityIndicator color={THEME.primary} />
                                ) : (
                                    <View style={styles.uploadIconContainer}>
                                        <Ionicons name="cloud-upload-outline" size={20} color={THEME.primary} />
                                        <Text style={{ fontSize: 10, color: THEME.primary, fontWeight: '600' }}>UPLOAD</Text>
                                    </View>
                                )}
                            </TouchableOpacity>
                        </View>

                        {/* Back Side */}
                        {req.requires_back_side && (
                            <View style={[styles.uploadRow, { marginTop: 15, borderTopWidth: 1, borderTopColor: '#F3F4F6', paddingTop: 15 }]}>
                                <View style={{ flex: 1, marginRight: 10 }}>
                                    <Text style={styles.sideLabel}>Back Side</Text>
                                    {(() => {
                                        const doc = getDocStatus(req.id, 'back');
                                        if (doc === 'missing') return renderStatusBadge('missing');
                                        return (
                                            <View>
                                                {renderStatusBadge(doc.status, doc.rejection_reason)}
                                                {doc.document_url && (
                                                    <TouchableOpacity style={styles.previewContainer}>
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
                                        <ActivityIndicator color={THEME.primary} />
                                    ) : (
                                        <View style={styles.uploadIconContainer}>
                                            <Ionicons name="cloud-upload-outline" size={20} color={THEME.primary} />
                                            <Text style={{ fontSize: 10, color: THEME.primary, fontWeight: '600' }}>UPLOAD</Text>
                                        </View>
                                    )}
                                </TouchableOpacity>
                            </View>
                        )}
                    </View>
                ))}
            </ScrollView>
        </View>
    );
}

const styles = StyleSheet.create({
    container: { flex: 1, backgroundColor: '#F9FAFB' },
    center: { justifyContent: 'center', alignItems: 'center' },
    header: {
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        paddingTop: Platform.OS === 'ios' ? 60 : 20,
        paddingBottom: 20,
        paddingHorizontal: 20,
        backgroundColor: '#fff',
        borderBottomWidth: 1,
        borderBottomColor: '#F3F4F6',
    },
    backBtn: { padding: 4 },
    headerTitle: { fontSize: 18, fontWeight: '700', color: '#1A1A1A' },
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
    infoText: { color: THEME.primaryDark, fontSize: 13, lineHeight: 20, flex: 1 },
    card: {
        backgroundColor: '#fff',
        borderRadius: 12,
        padding: 16,
        marginBottom: 16,
        borderWidth: 1,
        borderColor: '#E5E7EB',
        shadowColor: '#000',
        shadowOffset: { width: 0, height: 1 },
        shadowOpacity: 0.05,
        shadowRadius: 2,
        elevation: 2,
    },
    cardHeader: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 },
    cardTitle: { fontSize: 16, fontWeight: '600', color: '#1A1A1A' },
    mandatory: { color: THEME.error, fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
    cardDesc: { color: '#6B7280', fontSize: 13, marginBottom: 15 },
    uploadRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
    sideLabel: { color: '#374151', fontSize: 13, marginBottom: 4, fontWeight: '500' },
    badge: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 4, alignSelf: 'flex-start' },
    badgeText: { color: '#fff', fontSize: 10, fontWeight: '700', textTransform: 'uppercase' },
    rejectReason: { color: THEME.error, fontSize: 11, marginTop: 2 },
    uploadBtn: {
        padding: 8,
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
        marginTop: 8,
        borderRadius: 8,
        overflow: 'hidden',
        width: 100,
        height: 60,
        backgroundColor: '#F3F4F6',
        borderWidth: 1,
        borderColor: '#E5E7EB',
    },
    docPreview: {
        width: '100%',
        height: '100%',
    }
});
