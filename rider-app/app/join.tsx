import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, StyleSheet, Text, TouchableOpacity, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useAuthStore } from '@shared/store/authStore';
import { useTheme } from '@shared/theme/ThemeContext';
import type { ThemeColors } from '@shared/theme/index';
import { useWorkProfileStore } from '../store/workProfileStore';

/**
 * Company-invite deep link target (fast-follow FF2).
 *
 * app://join?token=… and https://spinr.app/join?token=… were registered in
 * app.config.ts but this route never existed — tapping an invite link on a
 * phone dead-ended. Accepts the invite for a signed-in rider and lands them
 * on their new Work Profile; signed-out users are told to sign in first
 * (the web portal link in the same email covers desktop).
 */
export default function JoinCompanyScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);
  const params = useLocalSearchParams<{ token?: string; invite_token?: string }>();
  const token = (params.token || params.invite_token || '').toString();
  const acceptInvite = useWorkProfileStore((s) => s.acceptInvite);

  const [state, setState] = useState<'checking' | 'unauthenticated' | 'accepting' | 'done' | 'error'>(
    'checking',
  );
  const [companyName, setCompanyName] = useState('');
  const [errorText, setErrorText] = useState('');

  const run = useCallback(async () => {
    if (!token) {
      setState('error');
      setErrorText('This invite link is missing its code. Ask your admin to resend it.');
      return;
    }
    if (!useAuthStore.getState().token) {
      setState('unauthenticated');
      return;
    }
    setState('accepting');
    try {
      const name = await acceptInvite(token);
      setCompanyName(name);
      setState('done');
    } catch (e: any) {
      const status = e?.response?.status;
      setState('error');
      if (status === 409) {
        setErrorText('This invite was already used. If that was you, your Work Profile is ready.');
      } else if (status === 404) {
        setErrorText('This invite is no longer valid. Ask your company admin for a new one.');
      } else {
        setErrorText('Could not accept the invite. Please try again.');
      }
    }
  }, [token, acceptInvite]);

  useEffect(() => {
    run();
  }, [run]);

  return (
    <View style={[styles.container, { paddingTop: insets.top + 24, paddingBottom: insets.bottom + 24 }]}>
      <View style={styles.card}>
        <View style={styles.iconWrap}>
          <Ionicons
            name={state === 'done' ? 'briefcase' : state === 'error' ? 'alert-circle' : 'briefcase-outline'}
            size={34}
            color={state === 'error' ? colors.danger ?? '#DC2626' : colors.primary}
          />
        </View>

        {(state === 'checking' || state === 'accepting') && (
          <>
            <ActivityIndicator color={colors.primary} style={{ marginVertical: 12 }} />
            <Text style={styles.title}>Joining your company…</Text>
          </>
        )}

        {state === 'unauthenticated' && (
          <>
            <Text style={styles.title}>Sign in to accept</Text>
            <Text style={styles.subtitle}>
              Sign in with your phone number first, then tap the invite link in your email again.
            </Text>
            <TouchableOpacity style={styles.button} onPress={() => router.replace('/login' as any)}>
              <Text style={styles.buttonText}>Go to sign in</Text>
            </TouchableOpacity>
          </>
        )}

        {state === 'done' && (
          <>
            <Text style={styles.title}>Welcome to {companyName}!</Text>
            <Text style={styles.subtitle}>
              Work Mode is on — work rides are billed to your employer within your allowance.
            </Text>
            <TouchableOpacity
              style={styles.button}
              onPress={() => router.replace('/work-profile' as any)}
            >
              <Text style={styles.buttonText}>Open Work Profile</Text>
            </TouchableOpacity>
          </>
        )}

        {state === 'error' && (
          <>
            <Text style={styles.title}>Invite problem</Text>
            <Text style={styles.subtitle}>{errorText}</Text>
            <TouchableOpacity style={styles.button} onPress={() => router.replace('/(tabs)' as any)}>
              <Text style={styles.buttonText}>Back to home</Text>
            </TouchableOpacity>
          </>
        )}
      </View>
    </View>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
      alignItems: 'center',
      justifyContent: 'center',
      paddingHorizontal: 24,
    },
    card: { alignItems: 'center', maxWidth: 340 },
    iconWrap: {
      backgroundColor: `${colors.primary}14`,
      borderRadius: 20,
      padding: 16,
      marginBottom: 12,
    },
    title: {
      fontSize: 20,
      fontWeight: '700',
      color: colors.text,
      textAlign: 'center',
      marginTop: 4,
    },
    subtitle: {
      fontSize: 14,
      color: colors.textDim,
      textAlign: 'center',
      marginTop: 8,
      lineHeight: 20,
    },
    button: {
      backgroundColor: colors.primary,
      borderRadius: 14,
      paddingHorizontal: 24,
      paddingVertical: 14,
      marginTop: 20,
    },
    buttonText: { color: '#fff', fontSize: 15, fontWeight: '700' },
  });
}
