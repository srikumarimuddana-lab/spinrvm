import React, { useEffect, useState, useCallback, useMemo } from 'react';
import {
  View,
  Text,
  StyleSheet,
  TouchableOpacity,
  ScrollView,
  TextInput,
  KeyboardAvoidingView,
  Platform,
  ActivityIndicator,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import api from '@shared/api/client';
import CustomAlert from '@shared/components/CustomAlert';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';

const MAX_CONTACTS = 3;

interface EmergencyContact {
  id: string;
  name: string;
  phone: string;
  relationship?: string;
}

const RELATIONSHIPS = [
  'Spouse', 'Parent', 'Sibling', 'Child', 'Friend', 'Other',
];

export default function EmergencyContactsScreen() {
  const router = useRouter();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const [contacts, setContacts] = useState<EmergencyContact[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);

  // Form state
  const [name, setName] = useState('');
  const [phone, setPhone] = useState('');
  const [relationship, setRelationship] = useState('Friend');
  const [saving, setSaving] = useState(false);
  const [alertState, setAlertState] = useState<{
    visible: boolean;
    title: string;
    message: string;
    variant: 'info' | 'warning' | 'danger' | 'success';
    buttons?: Array<{ text: string; style?: 'default' | 'cancel' | 'destructive'; onPress?: () => void }>;
  }>({ visible: false, title: '', message: '', variant: 'info' });

  const fetchContacts = useCallback(async () => {
    try {
      const res = await api.get('/users/emergency-contacts');
      setContacts(res.data?.contacts || []);
    } catch {
      console.error('Failed to fetch emergency contacts');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchContacts();
  }, [fetchContacts]);

  const handleAdd = async () => {
    const trimmedName = name.trim();
    const trimmedPhone = phone.trim().replace(/\D/g, '');

    if (!trimmedName) {
      setAlertState({ visible: true, title: 'Missing Name', message: 'Please enter a contact name.', variant: 'warning' });
      return;
    }
    if (trimmedPhone.length < 10) {
      setAlertState({ visible: true, title: 'Invalid Phone', message: 'Please enter a valid phone number (at least 10 digits).', variant: 'warning' });
      return;
    }

    setSaving(true);
    try {
      await api.post('/users/emergency-contacts', {
        name: trimmedName,
        phone: trimmedPhone,
        relationship,
      });
      setShowAdd(false);
      setName('');
      setPhone('');
      setRelationship('Friend');
      await fetchContacts();
      setAlertState({ visible: true, title: 'Contact Added', message: `${trimmedName} has been added as an emergency contact.`, variant: 'success' });
    } catch (error: any) {
      const msg = error?.response?.data?.detail || 'Could not add contact.';
      setAlertState({ visible: true, title: 'Error', message: msg, variant: 'danger' });
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = (contact: EmergencyContact) => {
    setAlertState({
      visible: true,
      title: 'Remove Contact',
      message: `Remove ${contact.name} as an emergency contact?`,
      variant: 'warning',
      buttons: [
        { text: 'Cancel', style: 'cancel' },
        {
          text: 'Remove',
          style: 'destructive',
          onPress: async () => {
            try {
              await api.delete(`/users/emergency-contacts/${contact.id}`);
              await fetchContacts();
            } catch {
              setAlertState({ visible: true, title: 'Error', message: 'Could not remove contact.', variant: 'danger' });
            }
          },
        },
      ],
    });
  };

  const formatPhone = (raw: string) => {
    const cleaned = raw.replace(/\D/g, '');
    if (cleaned.length === 11 && cleaned.startsWith('1')) {
      return `+1 (${cleaned.slice(1, 4)}) ${cleaned.slice(4, 7)}-${cleaned.slice(7)}`;
    }
    if (cleaned.length === 10) {
      return `(${cleaned.slice(0, 3)}) ${cleaned.slice(3, 6)}-${cleaned.slice(6)}`;
    }
    return raw;
  };

  const getRelationshipIcon = (rel?: string): string => {
    switch (rel?.toLowerCase()) {
      case 'spouse': return 'heart';
      case 'parent': return 'people';
      case 'sibling': return 'people-outline';
      case 'child': return 'person';
      case 'friend': return 'person-outline';
      default: return 'person-circle-outline';
    }
  };

  return (
    <SafeAreaView style={styles.container} edges={['top']}>
      {/* Header */}
      <View style={styles.header}>
        <TouchableOpacity style={styles.backButton} onPress={() => router.back()}>
          <Ionicons name="arrow-back" size={24} color={colors.text} />
        </TouchableOpacity>
        <Text style={styles.headerTitle}>Emergency Contacts</Text>
        <View style={{ width: 44 }} />
      </View>

      <KeyboardAvoidingView
        style={{ flex: 1 }}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        <ScrollView style={styles.content} showsVerticalScrollIndicator={false}>
          {/* Info Banner */}
          <View style={styles.infoBanner}>
            <Ionicons name="shield-checkmark" size={24} color={colors.primary} />
            <Text style={styles.infoText}>
              Your emergency contacts will be notified automatically when you use the Emergency button during a ride. You can add up to {MAX_CONTACTS} contacts.
            </Text>
          </View>

          {/* Contacts List */}
          {loading ? (
            <View style={styles.loadingContainer}>
              <ActivityIndicator size="large" color={colors.primary} />
            </View>
          ) : contacts.length === 0 ? (
            <View style={styles.emptyState}>
              <View style={styles.emptyIcon}>
                <Ionicons name="people-outline" size={48} color="#CCC" />
              </View>
              <Text style={styles.emptyTitle}>No emergency contacts yet</Text>
              <Text style={styles.emptySubtitle}>
                Add a trusted contact so they can be reached in case of an emergency during your ride.
              </Text>
            </View>
          ) : (
            <View style={styles.contactsList}>
              {contacts.map((contact) => (
                <View key={contact.id} style={styles.contactCard}>
                  <View style={styles.contactAvatar}>
                    <Ionicons
                      name={getRelationshipIcon(contact.relationship) as any}
                      size={24}
                      color={colors.primary}
                    />
                  </View>
                  <View style={styles.contactInfo}>
                    <Text style={styles.contactName}>{contact.name}</Text>
                    <Text style={styles.contactPhone}>{formatPhone(contact.phone)}</Text>
                    {contact.relationship && (
                      <Text style={styles.contactRelationship}>{contact.relationship}</Text>
                    )}
                  </View>
                  <TouchableOpacity
                    style={styles.deleteButton}
                    onPress={() => handleDelete(contact)}
                  >
                    <Ionicons name="trash-outline" size={20} color="#EF4444" />
                  </TouchableOpacity>
                </View>
              ))}
            </View>
          )}

          {/* Add Contact Section */}
          {contacts.length < MAX_CONTACTS && !showAdd && (
            <TouchableOpacity
              style={styles.addButton}
              onPress={() => setShowAdd(true)}
            >
              <Ionicons name="add-circle" size={22} color={colors.primary} />
              <Text style={styles.addButtonText}>Add Emergency Contact</Text>
            </TouchableOpacity>
          )}

          {/* Add Contact Form */}
          {showAdd && (
            <View style={styles.addForm}>
              <Text style={styles.formTitle}>New Emergency Contact</Text>

              <Text style={styles.formLabel}>Full Name</Text>
              <TextInput
                style={styles.formInput}
                placeholder="e.g. Sarah Johnson"
                placeholderTextColor={colors.textDim}
                value={name}
                onChangeText={setName}
                autoCapitalize="words"
              />

              <Text style={styles.formLabel}>Phone Number</Text>
              <TextInput
                style={styles.formInput}
                placeholder="e.g. (306) 555-1234"
                placeholderTextColor={colors.textDim}
                value={phone}
                onChangeText={setPhone}
                keyboardType="phone-pad"
              />

              <Text style={styles.formLabel}>Relationship</Text>
              <View style={styles.relationshipRow}>
                {RELATIONSHIPS.map((rel) => (
                  <TouchableOpacity
                    key={rel}
                    style={[
                      styles.relationshipChip,
                      relationship === rel && styles.relationshipChipActive,
                    ]}
                    onPress={() => setRelationship(rel)}
                  >
                    <Text
                      style={[
                        styles.relationshipChipText,
                        relationship === rel && styles.relationshipChipTextActive,
                      ]}
                    >
                      {rel}
                    </Text>
                  </TouchableOpacity>
                ))}
              </View>

              <View style={styles.formButtons}>
                <TouchableOpacity
                  style={styles.cancelButton}
                  onPress={() => { setShowAdd(false); setName(''); setPhone(''); }}
                >
                  <Text style={styles.cancelButtonText}>Cancel</Text>
                </TouchableOpacity>
                <TouchableOpacity
                  style={[styles.saveButton, saving && { opacity: 0.6 }]}
                  onPress={handleAdd}
                  disabled={saving}
                >
                  {saving ? (
                    <ActivityIndicator color="#FFF" size="small" />
                  ) : (
                    <Text style={styles.saveButtonText}>Save Contact</Text>
                  )}
                </TouchableOpacity>
              </View>
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
      <CustomAlert
        visible={alertState.visible}
        title={alertState.title}
        message={alertState.message}
        variant={alertState.variant}
        buttons={alertState.buttons || [{ text: 'OK', style: 'default' }]}
        onClose={() => setAlertState(prev => ({ ...prev, visible: false }))}
      />
    </SafeAreaView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    header: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      paddingHorizontal: 16,
      paddingVertical: 12,
      backgroundColor: colors.surface,
      borderBottomWidth: 1,
      borderBottomColor: colors.border,
    },
    backButton: {
      width: 44,
      height: 44,
      justifyContent: 'center',
      alignItems: 'center',
    },
    headerTitle: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    content: {
      flex: 1,
      padding: 20,
    },
    infoBanner: {
      flexDirection: 'row',
      backgroundColor: colors.primary + '10',
      borderRadius: 16,
      padding: 16,
      marginBottom: 24,
      alignItems: 'flex-start',
      gap: 12,
    },
    infoText: {
      flex: 1,
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textSecondary,
      lineHeight: 20,
    },
    loadingContainer: {
      paddingVertical: 60,
      alignItems: 'center',
    },
    emptyState: {
      alignItems: 'center',
      paddingVertical: 40,
    },
    emptyIcon: {
      width: 80,
      height: 80,
      borderRadius: 40,
      backgroundColor: colors.surfaceLight,
      justifyContent: 'center',
      alignItems: 'center',
      marginBottom: 16,
    },
    emptyTitle: {
      fontSize: 18,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
      marginBottom: 8,
    },
    emptySubtitle: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      textAlign: 'center',
      lineHeight: 20,
      paddingHorizontal: 20,
    },
    contactsList: {
      gap: 12,
    },
    contactCard: {
      flexDirection: 'row',
      alignItems: 'center',
      backgroundColor: colors.surfaceLight,
      borderRadius: 16,
      padding: 16,
      borderWidth: 1,
      borderColor: colors.border,
    },
    contactAvatar: {
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: colors.primary + '15',
      justifyContent: 'center',
      alignItems: 'center',
      marginRight: 14,
    },
    contactInfo: {
      flex: 1,
    },
    contactName: {
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.text,
    },
    contactPhone: {
      fontSize: 14,
      fontFamily: 'PlusJakartaSans_400Regular',
      color: colors.textDim,
      marginTop: 2,
    },
    contactRelationship: {
      fontSize: 12,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.primary,
      marginTop: 4,
      textTransform: 'capitalize',
    },
    deleteButton: {
      width: 40,
      height: 40,
      borderRadius: 20,
      backgroundColor: '#FEF2F2',
      justifyContent: 'center',
      alignItems: 'center',
    },
    addButton: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      paddingVertical: 18,
      marginTop: 16,
      borderWidth: 2,
      borderColor: colors.primary + '30',
      borderRadius: 16,
      borderStyle: 'dashed',
    },
    addButtonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.primary,
    },
    addForm: {
      marginTop: 20,
      backgroundColor: colors.surfaceLight,
      borderRadius: 20,
      padding: 20,
      borderWidth: 1,
      borderColor: colors.border,
    },
    formTitle: {
      fontSize: 17,
      fontFamily: 'PlusJakartaSans_700Bold',
      color: colors.text,
      marginBottom: 20,
    },
    formLabel: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim,
      marginBottom: 6,
      marginTop: 14,
    },
    formInput: {
      backgroundColor: colors.surface,
      borderWidth: 1.5,
      borderColor: colors.border,
      borderRadius: 12,
      paddingHorizontal: 16,
      paddingVertical: 14,
      fontSize: 16,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.text,
    },
    relationshipRow: {
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 8,
      marginTop: 4,
    },
    relationshipChip: {
      paddingHorizontal: 14,
      paddingVertical: 8,
      borderRadius: 20,
      backgroundColor: colors.surface,
      borderWidth: 1.5,
      borderColor: colors.border,
    },
    relationshipChipActive: {
      backgroundColor: colors.primary + '12',
      borderColor: colors.primary,
    },
    relationshipChipText: {
      fontSize: 13,
      fontFamily: 'PlusJakartaSans_500Medium',
      color: colors.textDim,
    },
    relationshipChipTextActive: {
      color: colors.primary,
    },
    formButtons: {
      flexDirection: 'row',
      gap: 12,
      marginTop: 24,
    },
    cancelButton: {
      flex: 1,
      paddingVertical: 14,
      borderRadius: 14,
      backgroundColor: colors.border,
      alignItems: 'center',
    },
    cancelButtonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: colors.textDim,
    },
    saveButton: {
      flex: 2,
      paddingVertical: 14,
      borderRadius: 14,
      backgroundColor: colors.primary,
      alignItems: 'center',
    },
    saveButtonText: {
      fontSize: 15,
      fontFamily: 'PlusJakartaSans_600SemiBold',
      color: '#FFFFFF',
    },
  });
}
