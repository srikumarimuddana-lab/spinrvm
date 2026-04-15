import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  Platform,
  Image,
  StatusBar,
  ActivityIndicator,
  Modal,
  TextInput,
  KeyboardAvoidingView,
  Keyboard,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons, MaterialCommunityIcons, FontAwesome5 } from '@expo/vector-icons';
import { LinearGradient } from 'expo-linear-gradient';
import { useRouter } from 'expo-router';
import { useFocusEffect } from '@react-navigation/native';
import * as ImagePicker from 'expo-image-picker';
import { useAuthStore } from '@shared/store/authStore';
import api from '@shared/api/client';
import SpinrConfig from '@shared/config/spinr.config';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

export default function ProfileScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { user, driver: driverData, logout, fetchDriverProfile, updateProfileImage } = useAuthStore();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const modalStyles = useMemo(() => createModalStyles(colors), [colors]);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isUploadingPhoto, setIsUploadingPhoto] = useState(false);
  const [docRequirements, setDocRequirements] = useState<Array<{id: string; name: string; description?: string}>>([]);

  // Edit modal state
  const [showEditModal, setShowEditModal] = useState(false);
  const [editFirstName, setEditFirstName] = useState('');
  const [editLastName, setEditLastName] = useState('');
  const [editEmail, setEditEmail] = useState('');
  const [editGender, setEditGender] = useState('');
  const [showGenderPicker, setShowGenderPicker] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  // Custom alert state
  const [showLogoutAlert, setShowLogoutAlert] = useState(false);
  const [showPhotoPickerAlert, setShowPhotoPickerAlert] = useState(false);
  const [feedbackAlert, setFeedbackAlert] = useState<{
    visible: boolean; title: string; message?: string;
    variant: 'info' | 'success' | 'danger' | 'warning';
  }>({ visible: false, title: '', variant: 'info' });

  const showFeedback = (title: string, message: string, variant: 'success' | 'danger' | 'warning' | 'info' = 'info') => {
    setFeedbackAlert({ visible: true, title, message, variant });
  };

  const genderOptions = [
    { label: 'Male', value: 'Male' },
    { label: 'Female', value: 'Female' },
    { label: 'Other', value: 'Other' },
  ];

  // Re-fetch user + driver data every time this tab comes into focus
  useFocusEffect(
    useCallback(() => {
      let cancelled = false;

      const refreshProfile = async () => {
        setIsRefreshing(true);
        try {
          const userRes = await api.get('/auth/me');
          if (!cancelled && userRes.data) useAuthStore.setState({ user: userRes.data });

          try {
            const driverRes = await api.get('/drivers/me');
            if (!cancelled && driverRes.data) useAuthStore.setState({ driver: driverRes.data });
          } catch (driverErr) {} // ignore if no driver

          try {
            const reqRes = await api.get('/drivers/requirements');
            if (!cancelled && reqRes.data) setDocRequirements(reqRes.data);
          } catch (reqErr) {}
        } finally {
          if (!cancelled) setIsRefreshing(false);
        }
      };

      refreshProfile();
      return () => { cancelled = true; };
    }, [])
  );

  const handlePickPhoto = () => setShowPhotoPickerAlert(true);

  const launchCamera = async () => {
    const { status } = await ImagePicker.requestCameraPermissionsAsync();
    if (status !== 'granted') return showFeedback('Permission Denied', 'Camera access is needed.', 'warning');
    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.7,
    });
    if (!result.canceled && result.assets[0]) uploadPhoto(result.assets[0].uri);
  };

  const launchGallery = async () => {
    const { status } = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (status !== 'granted') return showFeedback('Permission Denied', 'Library access is needed.', 'warning');
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'], allowsEditing: true, aspect: [1, 1], quality: 0.7,
    });
    if (!result.canceled && result.assets[0]) uploadPhoto(result.assets[0].uri);
  };

  const uploadPhoto = async (uri: string) => {
    setIsUploadingPhoto(true);
    try {
      await updateProfileImage(uri);
      showFeedback('Photo Updated', 'Your profile photo has been submitted for review.', 'success');
    } catch (err: any) {
      showFeedback('Upload Failed', err.message || 'Failed to upload', 'danger');
    } finally {
      setIsUploadingPhoto(false);
    }
  };

  const openEditModal = () => {
    setEditFirstName(user?.first_name || '');
    setEditLastName(user?.last_name || '');
    setEditEmail(user?.email || '');
    setEditGender(user?.gender || '');
    setShowGenderPicker(false);
    setShowEditModal(true);
  };

  const handleSaveProfile = async () => {
    if (!editFirstName.trim() || !editLastName.trim() || !editEmail.trim() || !editGender) {
      return showFeedback('Missing Info', 'Please fill in all fields', 'warning');
    }
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!re.test(editEmail)) return showFeedback('Invalid Email', 'Please enter a valid email address', 'warning');

    Keyboard.dismiss();
    setIsSaving(true);
    try {
      const res = await api.post('/users/profile', {
        first_name: editFirstName.trim(),
        last_name: editLastName.trim(),
        email: editEmail.trim().toLowerCase(),
        gender: editGender,
      });
      if (res.data) useAuthStore.setState({ user: res.data });
      setShowEditModal(false);
      showFeedback('Profile Updated', 'Your information has been saved.', 'success');
    } catch (err: any) {
      showFeedback('Update Failed', err.message || 'Failed to update', 'danger');
    } finally {
      setIsSaving(false);
    }
  };

  const handleLogout = () => {
    setShowLogoutAlert(true);
  };

  const ratingElements = (rating: number) => {
    const stars = [];
    for (let i = 1; i <= 5; i++) {
        stars.push(
            <Ionicons
              key={i}
              name={i <= Math.round(rating) ? 'star' : 'star-outline'}
              size={14}
              color={i <= Math.round(rating) ? '#FFD700' : 'rgba(255,255,255,0.3)'}
            />
        );
    }
    return stars;
  };

  return (
    <View style={styles.container}>
      <StatusBar barStyle="light-content" />
      <ScrollView contentContainerStyle={{ paddingBottom: insets.bottom + 90 }} showsVerticalScrollIndicator={false}>
        
        {/* Premium Header */}
        <LinearGradient
            colors={[colors.primary, colors.primaryDark]}
            style={[styles.headerHero, { paddingTop: insets.top + 20 }]}
        >
          {isRefreshing && (
            <View style={{ position: 'absolute', top: insets.top + 10, right: 20 }}>
              <ActivityIndicator size="small" color="#fff" />
            </View>
          )}

          <TouchableOpacity style={styles.avatarContainer} onPress={handlePickPhoto} activeOpacity={0.8}>
            {isUploadingPhoto ? (
              <View style={[styles.avatarPlaceholder, { backgroundColor: 'rgba(255,255,255,0.2)' }]}>
                <ActivityIndicator size="large" color="#fff" />
              </View>
            ) : user?.profile_image ? (
              <Image source={{ uri: user.profile_image }} style={[
                styles.avatar,
                user.profile_image_status === 'pending_review' && { opacity: 0.7 },
              ]} />
            ) : (
              <View style={[styles.avatarPlaceholder, { backgroundColor: 'rgba(255,255,255,0.2)' }]}>
                <Ionicons name="person" size={40} color="#fff" />
              </View>
            )}
            <View style={styles.cameraButton}>
              <Ionicons name="camera" size={14} color={colors.primary} />
            </View>
            <View style={styles.verifiedBadge}>
              <Ionicons
                name={driverData?.is_verified ? 'checkmark-circle' : 'time-outline'}
                size={20}
                color={driverData?.is_verified ? '#10B981' : '#F59E0B'}
              />
            </View>
          </TouchableOpacity>

          {/* Photo review alerts inside hero */}
          {user?.profile_image_status === 'pending_review' && (
            <View style={styles.photoStatusBanner}>
              <Ionicons name="time-outline" size={14} color="#fff" />
              <Text style={styles.photoStatusText}>Photo pending review</Text>
            </View>
          )}
          {user?.profile_image_status === 'rejected' && (
            <View style={[styles.photoStatusBanner, { backgroundColor: 'rgba(239, 68, 68, 0.9)' }]}>
              <Ionicons name="close-circle" size={14} color="#fff" />
              <Text style={styles.photoStatusText}>Photo rejected — update needed</Text>
            </View>
          )}

          <Text style={styles.name}>
            {driverData?.name || (user?.first_name ? `${user.first_name} ${user.last_name || ''}` : 'Driver')}
          </Text>
          <Text style={styles.subtitle}>
            {driverData?.is_verified ? 'Verified Driver' : 'Pending Verification'}
          </Text>

          <View style={styles.ratingHeroContainer}>
             <View style={styles.ratingBox}>
                 <Text style={styles.ratingNumber}>{(driverData?.rating || user?.rating || 5.0).toFixed(1)}</Text>
                 <View style={styles.starsRow}>{ratingElements(driverData?.rating || user?.rating || 5)}</View>
             </View>
             <View style={styles.ratingDivider} />
             <View style={styles.ratingBox}>
                 <Text style={styles.ratingNumber}>{driverData?.total_rides || 0}</Text>
                 <Text style={styles.ratingLabel}>Trips</Text>
             </View>
          </View>
        </LinearGradient>

        <View style={styles.contentBody}>
            {/* Personal Info */}
            <View style={styles.section}>
            <View style={styles.sectionHeaderRow}>
                <Text style={styles.sectionTitle}>Personal Info</Text>
                <TouchableOpacity onPress={openEditModal} style={styles.editBtn}>
                    <Text style={styles.editBtnText}>Edit</Text>
                </TouchableOpacity>
            </View>
            <View style={styles.card}>
                <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(239, 68, 68, 0.1)' }]}>
                    <Ionicons name="call" size={16} color={colors.primary} />
                </View>
                <View style={styles.cardInfo}>
                    <Text style={styles.cardLabel}>Phone</Text>
                    <Text style={styles.cardValue}>{user?.phone || 'N/A'}</Text>
                </View>
                </View>
                <View style={styles.cardDivider} />
                <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(56, 189, 248, 0.1)' }]}>
                    <Ionicons name="mail" size={16} color="#38BDF8" />
                </View>
                <View style={styles.cardInfo}>
                    <Text style={styles.cardLabel}>Email</Text>
                    <Text style={styles.cardValue}>{user?.email || 'N/A'}</Text>
                </View>
                </View>
                {user?.gender && (
                <>
                    <View style={styles.cardDivider} />
                    <View style={styles.cardRow}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
                        <Ionicons name="person" size={16} color={'#F59E0B'} />
                    </View>
                    <View style={styles.cardInfo}>
                        <Text style={styles.cardLabel}>Gender</Text>
                        <Text style={styles.cardValue}>{user.gender}</Text>
                    </View>
                    </View>
                </>
                )}
            </View>
            </View>

            {driverData?.rejection_reason && !driverData.is_verified && (
            <View style={styles.rejectionBox}>
                <Ionicons name="alert-circle" size={24} color={'#EF4444'} />
                <View style={{flex: 1}}>
                    <Text style={styles.rejectionTitle}>Application Rejected</Text>
                    <Text style={styles.rejectionText}>{driverData.rejection_reason}</Text>
                </View>
            </View>
            )}

            {/* Vehicle Info */}
            <View style={styles.section}>
            <View style={styles.sectionHeaderRow}>
                <Text style={styles.sectionTitle}>Vehicle</Text>
                <TouchableOpacity onPress={() => router.push('/vehicle-info' as any)} style={styles.editBtn}>
                    <Text style={styles.editBtnText}>Edit</Text>
                </TouchableOpacity>
            </View>
            <TouchableOpacity style={styles.card} activeOpacity={0.8} onPress={() => router.push('/vehicle-info' as any)}>
                <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(16, 185, 129, 0.1)' }]}>
                    <FontAwesome5 name="car" size={16} color={'#10B981'} />
                </View>
                <View style={styles.cardInfo}>
                    <Text style={styles.cardLabel}>Vehicle</Text>
                    <Text style={styles.cardValue}>
                    {driverData?.vehicle_color} {driverData?.vehicle_make} {driverData?.vehicle_model}
                    </Text>
                </View>
                </View>
                <View style={styles.cardDivider} />
                <View style={styles.cardRow}>
                <View style={[styles.iconBox, { backgroundColor: 'rgba(99, 102, 241, 0.1)' }]}>
                    <MaterialCommunityIcons name="card-text" size={16} color="#6366F1" />
                </View>
                <View style={styles.cardInfo}>
                    <Text style={styles.cardLabel}>License Plate</Text>
                    <Text style={styles.cardValue}>{driverData?.license_plate || 'N/A'}</Text>
                </View>
                </View>
                {driverData?.vehicle_year && (
                <>
                    <View style={styles.cardDivider} />
                    <View style={styles.cardRow}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(56, 189, 248, 0.1)' }]}>
                        <Ionicons name="calendar" size={16} color="#38BDF8" />
                    </View>
                    <View style={styles.cardInfo}>
                        <Text style={styles.cardLabel}>Year</Text>
                        <Text style={styles.cardValue}>{driverData.vehicle_year}</Text>
                    </View>
                    </View>
                </>
                )}
            </TouchableOpacity>
            </View>

            {/* Documents */}
            <View style={styles.section}>
            <View style={styles.sectionHeaderRow}>
                <Text style={styles.sectionTitle}>Documents</Text>
                <TouchableOpacity onPress={() => router.push('/documents' as any)} style={styles.editBtn}>
                    <Text style={styles.editBtnText}>Manage</Text>
                </TouchableOpacity>
            </View>
            <TouchableOpacity style={styles.card} activeOpacity={0.8} onPress={() => router.push('/documents' as any)}>
                {docRequirements.length === 0 ? (
                <View style={styles.cardRow}>
                    <Ionicons name="document-text-outline" size={16} color={colors.textDim} />
                    <Text style={[styles.cardValueDim, { marginLeft: 8 }]}>No document requirements found</Text>
                </View>
                ) : docRequirements.map((req, i) => {
                const n = req.name.toLowerCase();
                const expiryKey =
                    n.includes('licen') ? 'license_expiry_date' :
                    n.includes('insurance') ? 'insurance_expiry_date' :
                    n.includes('background') ? 'background_check_expiry_date' :
                    n.includes('inspection') ? 'vehicle_inspection_expiry_date' :
                    n.includes('eligib') || n.includes('work permit') ? 'work_eligibility_expiry_date' : '';

                const icon: any =
                    n.includes('licen') ? 'id-card-outline' :
                    n.includes('insurance') ? 'shield-checkmark-outline' :
                    n.includes('background') ? 'document-text-outline' :
                    n.includes('inspection') ? 'car-sport-outline' : 'document-outline';

                const expiry = expiryKey ? driverData?.[expiryKey] : null;
                const isExpired = expiry ? new Date(expiry) < new Date() : false;
                const expiresIn = expiry ? Math.ceil((new Date(expiry).getTime() - Date.now()) / (1000 * 60 * 60 * 24)) : null;
                const isValid = expiry && !isExpired;
                const isExpiringSoon = expiresIn !== null && expiresIn > 0 && expiresIn < 30;

                return (
                    <React.Fragment key={req.id}>
                    {i > 0 && <View style={styles.cardDivider} />}
                    <View style={styles.cardRow}>
                        <View style={[
                            styles.iconBox,
                            isExpired ? { backgroundColor: 'rgba(239, 68, 68, 0.1)' } :
                            isValid ? { backgroundColor: 'rgba(16, 185, 129, 0.1)' } :
                            { backgroundColor: '#F9FAFB' },
                        ]}>
                        <Ionicons name={icon} size={16} color={isExpired ? '#EF4444' : isValid ? '#10B981' : colors.textDim} />
                        </View>
                        <View style={styles.cardInfo}>
                        <Text style={styles.cardLabel}>{req.name}</Text>
                        {expiry ? (
                            <View style={{flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 2}}>
                                <Text style={styles.cardValue}>{new Date(expiry).toLocaleDateString()}</Text>
                                <View style={[styles.docStatusBadge, isExpired ? {backgroundColor: '#EF4444'} : isExpiringSoon ? {backgroundColor: '#F59E0B'} : {backgroundColor: '#10B981'}]}>
                                    <Text style={styles.docStatusText}>
                                        {isExpired ? 'EXPIRED' : isExpiringSoon ? `Exp in ${expiresIn}d` : 'VALID'}
                                    </Text>
                                </View>
                            </View>
                        ) : (
                            <Text style={styles.cardValueDim}>Not submitted</Text>
                        )}
                        </View>
                        <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                    </View>
                    </React.Fragment>
                );
                })}
            </TouchableOpacity>
            </View>

            {/* Quick Actions */}
            <View style={styles.section}>
            <Text style={styles.sectionTitle}>Settings</Text>
            <View style={styles.card}>
                <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={() => router.push('/driver/notifications' as any)}>
                    <View style={[styles.iconBox, { backgroundColor: colors.surfaceLight }]}>
                        <Ionicons name="help-circle" size={18} color={colors.textDim} />
                    </View>
                    <Text style={styles.actionText}>Help Center</Text>
                    <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                </TouchableOpacity>
                <View style={styles.cardDivider} />
                <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={() => router.push('/driver/quests' as any)}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(139, 92, 246, 0.1)' }]}>
                        <Ionicons name="trophy" size={18} color="#8B5CF6" />
                    </View>
                    <Text style={styles.actionText}>Quests & Bonuses</Text>
                    <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                </TouchableOpacity>
                <View style={styles.cardDivider} />
                <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={() => router.push('/driver/referral' as any)}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(245, 158, 11, 0.1)' }]}>
                        <Ionicons name="gift" size={18} color={'#F59E0B'} />
                    </View>
                    <Text style={styles.actionText}>Referral Program</Text>
                    <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                </TouchableOpacity>
                <View style={styles.cardDivider} />
                <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={() => router.push('/driver/settings' as any)}>
                    <View style={[styles.iconBox, { backgroundColor: colors.surfaceLight }]}>
                        <Ionicons name="settings" size={18} color={colors.textDim} />
                    </View>
                    <Text style={styles.actionText}>App Settings</Text>
                    <Ionicons name="chevron-forward" size={18} color="#D1D5DB" />
                </TouchableOpacity>
                <View style={styles.cardDivider} />
                <TouchableOpacity style={styles.actionRow} activeOpacity={0.7} onPress={handleLogout}>
                    <View style={[styles.iconBox, { backgroundColor: 'rgba(239, 68, 68, 0.05)' }]}>
                        <Ionicons name="log-out" size={18} color={'#EF4444'} />
                    </View>
                    <Text style={[styles.actionText, { color: '#EF4444' }]}>Sign Out</Text>
                </TouchableOpacity>
            </View>
            </View>
        </View>
      </ScrollView>

      {/* Edit Profile Modal — mirrors vehicle-info.tsx visual language:
          hero card, section-grouped cards with inline uppercase labels,
          info banner, bottom-sheet gender picker, sticky save footer. */}
      <Modal
        visible={showEditModal}
        animationType="slide"
        presentationStyle="pageSheet"
        onRequestClose={() => setShowEditModal(false)}
      >
        <View style={modalStyles.container}>
          <LinearGradient colors={[colors.surface, '#F8F9FA']} style={StyleSheet.absoluteFill} />

          {/* Header */}
          <View style={modalStyles.header}>
            <TouchableOpacity onPress={() => setShowEditModal(false)} style={modalStyles.backBtn}>
              <Ionicons name="arrow-back" size={24} color={colors.text} />
            </TouchableOpacity>
            <Text style={modalStyles.headerTitle}>Personal Information</Text>
            <View style={{ width: 32 }} />
          </View>

          <KeyboardAvoidingView
            style={{ flex: 1 }}
            behavior={Platform.OS === 'ios' ? 'padding' : undefined}
          >
            <ScrollView
              contentContainerStyle={[modalStyles.content, { paddingBottom: insets.bottom + 140 }]}
              keyboardShouldPersistTaps="handled"
              showsVerticalScrollIndicator={false}
            >
              {/* Hero card — mirrors vehicle-info hero */}
              <View style={modalStyles.heroCard}>
                <View style={modalStyles.heroIconWrap}>
                  <Ionicons name="person" size={28} color={colors.primary} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={modalStyles.heroTitle}>Your Profile</Text>
                  <Text style={modalStyles.heroSub} numberOfLines={1}>
                    {editFirstName || editLastName
                      ? `${editFirstName} ${editLastName}`.trim()
                      : 'Tell riders who you are'}
                  </Text>
                  {editEmail ? (
                    <Text style={modalStyles.heroEmail} numberOfLines={1}>{editEmail}</Text>
                  ) : null}
                </View>
              </View>

              {/* Info banner */}
              <View style={modalStyles.infoBox}>
                <Ionicons name="information-circle" size={18} color={colors.primary} />
                <Text style={modalStyles.infoText}>
                  Your riders will see this info when you accept their rides.
                  Changes save instantly and apply to all future rides.
                </Text>
              </View>

              {/* Section: Name */}
              <Text style={modalStyles.sectionTitle}>Your Name</Text>
              <View style={modalStyles.card}>
                <View style={modalStyles.field}>
                  <Text style={modalStyles.fieldLabel}>First Name *</Text>
                  <TextInput
                    style={modalStyles.fieldInput}
                    value={editFirstName}
                    onChangeText={setEditFirstName}
                    placeholder="John"
                    placeholderTextColor="#B0B7C0"
                    autoCapitalize="words"
                    autoCorrect={false}
                  />
                </View>
                <View style={modalStyles.divider} />
                <View style={modalStyles.field}>
                  <Text style={modalStyles.fieldLabel}>Last Name *</Text>
                  <TextInput
                    style={modalStyles.fieldInput}
                    value={editLastName}
                    onChangeText={setEditLastName}
                    placeholder="Doe"
                    placeholderTextColor="#B0B7C0"
                    autoCapitalize="words"
                    autoCorrect={false}
                  />
                </View>
              </View>

              {/* Section: Contact & Identity */}
              <Text style={modalStyles.sectionTitle}>Contact & Identity</Text>
              <View style={modalStyles.card}>
                <View style={modalStyles.field}>
                  <Text style={modalStyles.fieldLabel}>Email Address *</Text>
                  <TextInput
                    style={modalStyles.fieldInput}
                    value={editEmail}
                    onChangeText={setEditEmail}
                    placeholder="john.doe@example.com"
                    placeholderTextColor="#B0B7C0"
                    keyboardType="email-address"
                    autoCapitalize="none"
                    autoCorrect={false}
                  />
                  <Text style={modalStyles.fieldHelper}>
                    Used for receipts, account recovery, and tax documents.
                  </Text>
                </View>
                <View style={modalStyles.divider} />
                <TouchableOpacity
                  style={modalStyles.pickerBox}
                  onPress={() => setShowGenderPicker(true)}
                  activeOpacity={0.7}
                >
                  <View style={modalStyles.pickerIconBox}>
                    <Ionicons
                      name={
                        editGender === 'Female'
                          ? 'female'
                          : editGender === 'Male'
                          ? 'male'
                          : 'person-outline'
                      }
                      size={22}
                      color={colors.primary}
                    />
                  </View>
                  <View style={{ flex: 1 }}>
                    <Text style={modalStyles.pickerLabel}>Gender *</Text>
                    <Text
                      style={[
                        modalStyles.pickerValue,
                        !editGender && { color: '#B0B7C0', fontWeight: '500' },
                      ]}
                    >
                      {editGender || 'Tap to select'}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={20} color={colors.textDim} />
                </TouchableOpacity>
              </View>

              <View style={{ height: 20 }} />
            </ScrollView>

            {/* Sticky save footer */}
            <View
              style={[
                modalStyles.footer,
                { paddingBottom: Math.max(insets.bottom, 12) + 8 },
              ]}
            >
              <TouchableOpacity
                style={[
                  modalStyles.saveButton,
                  (!editFirstName.trim() ||
                    !editLastName.trim() ||
                    !editEmail.trim() ||
                    !editGender ||
                    isSaving) &&
                    modalStyles.saveButtonDisabled,
                ]}
                onPress={handleSaveProfile}
                disabled={
                  !editFirstName.trim() ||
                  !editLastName.trim() ||
                  !editEmail.trim() ||
                  !editGender ||
                  isSaving
                }
                activeOpacity={0.85}
              >
                {isSaving ? (
                  <ActivityIndicator color="#fff" />
                ) : (
                  <>
                    <Ionicons name="checkmark-circle" size={20} color="#fff" />
                    <Text style={modalStyles.saveButtonText}>Save Changes</Text>
                  </>
                )}
              </TouchableOpacity>
            </View>
          </KeyboardAvoidingView>

          {/* Gender picker bottom sheet — mirrors vehicle-type picker */}
          <Modal
            visible={showGenderPicker}
            transparent
            animationType="slide"
            onRequestClose={() => setShowGenderPicker(false)}
          >
            <View style={modalStyles.sheetOverlay}>
              <View style={[modalStyles.sheetContent, { paddingBottom: insets.bottom + 20 }]}>
                <View style={modalStyles.sheetHandle} />
                <View style={modalStyles.sheetHeader}>
                  <Text style={modalStyles.sheetTitle}>Select Gender</Text>
                  <TouchableOpacity
                    onPress={() => setShowGenderPicker(false)}
                    style={modalStyles.sheetCloseBtn}
                  >
                    <Ionicons name="close" size={22} color={colors.text} />
                  </TouchableOpacity>
                </View>
                {genderOptions.map((g) => {
                  const selected = editGender === g.value;
                  return (
                    <TouchableOpacity
                      key={g.value}
                      style={[
                        modalStyles.sheetOption,
                        selected && modalStyles.sheetOptionSelected,
                      ]}
                      onPress={() => {
                        setEditGender(g.value);
                        setShowGenderPicker(false);
                      }}
                      activeOpacity={0.7}
                    >
                      <View style={modalStyles.sheetOptionIcon}>
                        <Ionicons
                          name={
                            g.value === 'Female'
                              ? 'female'
                              : g.value === 'Male'
                              ? 'male'
                              : 'person-outline'
                          }
                          size={22}
                          color={colors.primary}
                        />
                      </View>
                      <Text style={modalStyles.sheetOptionName}>{g.label}</Text>
                      {selected && (
                        <Ionicons name="checkmark-circle" size={24} color={colors.primary} />
                      )}
                    </TouchableOpacity>
                  );
                })}
              </View>
            </View>
          </Modal>
        </View>
      </Modal>

      {/* Custom Alert Modals */}
      <CustomAlert
        visible={showLogoutAlert}
        title="Sign Out"
        message="Are you sure you want to sign out?"
        variant="danger"
        icon="log-out-outline"
        buttons={[
          { text: 'Cancel', style: 'cancel' },
          { text: 'Sign Out', style: 'destructive', onPress: async () => { await logout(); router.replace('/login' as any); } },
        ]}
        onClose={() => setShowLogoutAlert(false)}
      />
      <CustomAlert
        visible={showPhotoPickerAlert}
        title="Update Photo"
        message="Choose how to update your profile photo."
        variant="info"
        icon="camera-outline"
        buttons={[
          { text: 'Take Photo', style: 'default', onPress: launchCamera },
          { text: 'Library', style: 'default', onPress: launchGallery },
          { text: 'Cancel', style: 'cancel' },
        ]}
        onClose={() => setShowPhotoPickerAlert(false)}
      />
      <CustomAlert visible={feedbackAlert.visible} title={feedbackAlert.title} message={feedbackAlert.message} variant={feedbackAlert.variant} buttons={[{ text: 'OK', style: 'default' }]} onClose={() => setFeedbackAlert(prev => ({ ...prev, visible: false }))} />
    </View>
  );
}

function createStyles(colors: ThemeColors) { return StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: colors.surfaceLight,
  },
  headerHero: {
    paddingHorizontal: 20,
    paddingBottom: 40,
    borderBottomLeftRadius: 32,
    borderBottomRightRadius: 32,
    alignItems: 'center',
    shadowColor: colors.primaryDark,
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.2,
    shadowRadius: 20,
    elevation: 10,
    zIndex: 10,
  },
  avatarContainer: {
    position: 'relative',
    marginBottom: 16,
    marginTop: 10,
  },
  avatar: {
    width: 100,
    height: 100,
    borderRadius: 50,
    borderWidth: 4,
    borderColor: '#fff',
    backgroundColor: 'rgba(255,255,255,0.2)'
  },
  avatarPlaceholder: {
    width: 100,
    height: 100,
    borderRadius: 50,
    justifyContent: 'center',
    alignItems: 'center',
    borderWidth: 4,
    borderColor: 'rgba(255,255,255,0.3)',
  },
  cameraButton: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surface,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 8,
    elevation: 4,
  },
  verifiedBadge: {
    position: 'absolute',
    bottom: 0,
    right: 0,
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surface,
    justifyContent: 'center',
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.15,
    shadowRadius: 8,
    elevation: 4,
  },
  photoStatusBanner: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: 'rgba(0,0,0,0.15)',
    borderRadius: 20,
    paddingHorizontal: 12,
    paddingVertical: 6,
    marginBottom: 12,
  },
  photoStatusText: {
    fontSize: 12,
    fontWeight: '600',
    color: '#fff',
  },
  name: {
    color: '#fff',
    fontSize: 26,
    fontWeight: '900',
    letterSpacing: 0.5,
  },
  subtitle: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 14,
    marginTop: 2,
    fontWeight: '500',
  },
  ratingHeroContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.15)',
    borderRadius: 16,
    marginTop: 20,
    paddingVertical: 12,
    paddingHorizontal: 24,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.3)',
  },
  ratingBox: {
    alignItems: 'center',
    paddingHorizontal: 12,
  },
  ratingDivider: {
    width: 1,
    height: 30,
    backgroundColor: 'rgba(255,255,255,0.3)',
    marginHorizontal: 12,
  },
  ratingNumber: {
    color: '#fff',
    fontSize: 22,
    fontWeight: '900',
  },
  ratingLabel: {
    color: 'rgba(255,255,255,0.8)',
    fontSize: 11,
    fontWeight: '600',
    marginTop: 2,
    letterSpacing: 1,
  },
  starsRow: {
    flexDirection: 'row',
    marginTop: 4,
    gap: 2,
  },
  contentBody: {
    paddingTop: 10,
  },
  section: {
    paddingHorizontal: 16,
    marginTop: 20,
  },
  sectionHeaderRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 12,
    paddingHorizontal: 4,
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: '800',
    letterSpacing: 0.2,
  },
  editBtn: {
    backgroundColor: colors.surface,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: colors.border,
  },
  editBtnText: {
    color: colors.textDim,
    fontSize: 12,
    fontWeight: '700',
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 24,
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.02)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.04,
    shadowRadius: 16,
    elevation: 3,
  },
  cardRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 12,
    gap: 14,
  },
  cardDivider: {
    height: 1,
    backgroundColor: colors.surfaceLight,
    marginLeft: 50,
  },
  iconBox: {
    width: 40,
    height: 40,
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  cardInfo: {
    flex: 1,
  },
  cardLabel: {
    color: colors.textDim,
    fontSize: 11,
    fontWeight: '600',
    letterSpacing: 0.5,
    textTransform: 'uppercase',
  },
  cardValue: {
    color: colors.text,
    fontSize: 15,
    fontWeight: '700',
    marginTop: 2,
  },
  cardValueDim: {
    color: colors.textSecondary,
    fontSize: 14,
    fontWeight: '500',
    marginTop: 2,
  },
  docStatusBadge: {
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 6,
  },
  docStatusText: {
    color: '#fff',
    fontSize: 9,
    fontWeight: '800',
  },
  actionRow: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 10,
    gap: 14,
  },
  actionText: {
    flex: 1,
    color: colors.text,
    fontSize: 15,
    fontWeight: '600',
  },
  rejectionBox: {
    marginHorizontal: 16,
    marginTop: 24,
    backgroundColor: 'rgba(239, 68, 68, 0.1)',
    borderRadius: 16,
    padding: 16,
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    borderWidth: 1,
    borderColor: 'rgba(239, 68, 68, 0.2)',
  },
  rejectionTitle: {
    color: '#EF4444',
    fontSize: 15,
    fontWeight: '800',
    marginBottom: 4,
  },
  rejectionText: {
    color: '#991B1B',
    fontSize: 13,
    lineHeight: 18,
  },
}); }

// Mirrors vehicle-info.tsx visual language so personal-info editing and
// vehicle-info editing feel like the same screen family. If you update one,
// update the other — or extract a shared style module.
function createModalStyles(colors: ThemeColors) { return StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.surface },

  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingTop: 16,
    paddingBottom: 16,
    paddingHorizontal: 20,
    backgroundColor: colors.surface,
    borderBottomWidth: 1,
    borderBottomColor: '#F3F4F6',
  },
  backBtn: { padding: 4, width: 32 },
  headerTitle: { fontSize: 18, fontWeight: '700', color: colors.text, flex: 1, textAlign: 'center' },

  content: { padding: 20 },

  heroCard: {
    flexDirection: 'row',
    backgroundColor: colors.surface,
    padding: 18,
    borderRadius: 18,
    marginBottom: 16,
    alignItems: 'center',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 2,
  },
  heroIconWrap: {
    width: 52,
    height: 52,
    borderRadius: 14,
    backgroundColor: 'rgba(255,59,48,0.08)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 14,
  },
  heroTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textDim,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
  },
  heroSub: { fontSize: 16, fontWeight: '600', color: colors.text, marginTop: 2 },
  heroEmail: { fontSize: 13, color: colors.textDim, marginTop: 2 },

  infoBox: {
    flexDirection: 'row',
    backgroundColor: 'rgba(255,59,48,0.06)',
    padding: 12,
    borderRadius: 12,
    marginBottom: 20,
    alignItems: 'flex-start',
    borderWidth: 1,
    borderColor: 'rgba(255,59,48,0.15)',
    gap: 8,
  },
  infoText: { color: colors.primary, flex: 1, fontSize: 12, lineHeight: 16, fontWeight: '500' },

  sectionTitle: {
    fontSize: 12,
    fontWeight: '700',
    color: colors.textDim,
    letterSpacing: 0.8,
    textTransform: 'uppercase',
    marginBottom: 8,
    marginTop: 4,
    paddingHorizontal: 4,
  },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 16,
    marginBottom: 20,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.04,
    shadowRadius: 4,
    elevation: 1,
    overflow: 'hidden',
  },
  divider: { height: 1, backgroundColor: colors.surfaceLight, marginHorizontal: 16 },

  field: { paddingHorizontal: 16, paddingVertical: 12 },
  fieldLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textDim,
    letterSpacing: 0.6,
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  fieldInput: { fontSize: 16, color: colors.text, padding: 0, fontWeight: '500' },
  fieldHelper: { fontSize: 11, color: colors.textDim, marginTop: 4 },

  pickerBox: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingVertical: 14,
    gap: 12,
  },
  pickerIconBox: {
    width: 40,
    height: 40,
    borderRadius: 10,
    backgroundColor: 'rgba(255,59,48,0.08)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  pickerLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: colors.textDim,
    letterSpacing: 0.6,
    textTransform: 'uppercase',
  },
  pickerValue: { fontSize: 16, fontWeight: '600', color: colors.text, marginTop: 2 },

  // Sticky footer
  footer: {
    backgroundColor: colors.surface,
    paddingHorizontal: 20,
    paddingTop: 12,
    borderTopWidth: 1,
    borderTopColor: '#F3F4F6',
  },
  saveButton: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.primary,
    borderRadius: 14,
    paddingVertical: 16,
    gap: 8,
    shadowColor: colors.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 4,
  },
  saveButtonDisabled: {
    backgroundColor: '#D1D5DB',
    shadowOpacity: 0,
    elevation: 0,
  },
  saveButtonText: { color: '#fff', fontSize: 16, fontWeight: '700' },

  // Gender picker bottom sheet — mirrors vehicle-info vehicleTypePicker modal
  sheetOverlay: { flex: 1, backgroundColor: 'rgba(0,0,0,0.5)', justifyContent: 'flex-end' },
  sheetContent: {
    backgroundColor: colors.surface,
    borderTopLeftRadius: 24,
    borderTopRightRadius: 24,
    maxHeight: '75%',
  },
  sheetHandle: {
    width: 40,
    height: 4,
    backgroundColor: '#E5E7EB',
    borderRadius: 2,
    alignSelf: 'center',
    marginTop: 10,
  },
  sheetHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 20,
    paddingBottom: 12,
  },
  sheetTitle: { fontSize: 20, fontWeight: '700', color: colors.text },
  sheetCloseBtn: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surfaceLight,
    justifyContent: 'center',
    alignItems: 'center',
  },
  sheetOption: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 20,
    paddingVertical: 14,
    gap: 12,
    borderTopWidth: 1,
    borderTopColor: '#F3F4F6',
  },
  sheetOptionSelected: { backgroundColor: 'rgba(255,59,48,0.04)' },
  sheetOptionIcon: {
    width: 44,
    height: 44,
    borderRadius: 12,
    backgroundColor: 'rgba(255,59,48,0.08)',
    justifyContent: 'center',
    alignItems: 'center',
  },
  sheetOptionName: { flex: 1, fontSize: 16, fontWeight: '700', color: colors.text },
}); }
