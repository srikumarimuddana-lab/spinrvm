import React, { ReactNode, useMemo } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StyleSheet,
  TouchableWithoutFeedback,
  Keyboard,
  View,
  ViewStyle,
  StyleProp,
  ScrollViewProps,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme } from '../theme/ThemeContext';
import type { ThemeColors } from '../theme/index';

/**
 * FormScreen — single source of truth for input-screen layout.
 *
 * Owns:
 *   - KeyboardAvoidingView with the right per-platform behavior + offset
 *     (the wrong combination here is the cause of every "button under the
 *     keyboard on a small Android" bug we've shipped — see UI-REVIEW.md
 *     BLOCKER 1).
 *   - ScrollView with `keyboardShouldPersistTaps='handled'` so taps inside
 *     buttons don't collapse the keyboard before the press handler fires.
 *   - `keyboardDismissMode='on-drag'` so flicking the form down dismisses
 *     the keyboard (the iOS native gesture; harmless on Android).
 *   - `dismissOnTap` — tapping any non-input area dismisses the keyboard.
 *     Used everywhere except true full-screen forms where we want sticky
 *     keyboard.
 *   - SafeAreaInsets — top/bottom padding from the device, with a minimum
 *     `paddingBottom` so CTAs never hug the home gesture indicator.
 *
 * Usage:
 *
 *   <FormScreen>
 *     <Header />
 *     <FieldsBlock />
 *     <CTA />
 *   </FormScreen>
 *
 *   <FormScreen scrollable={false} dismissOnTap={false}>
 *     <ChatStream />
 *     <ChatInputBar />
 *   </FormScreen>
 *
 * What this component intentionally does NOT do:
 *   - It does not own the screen background color — pass `style` if you
 *     need a non-default surface; otherwise we read `colors.surface`.
 *   - It does not own the StatusBar — set it from the route layout.
 *   - It does not own focus management — use `useFocusEffect` +
 *     `inputRef.current?.focus()` in your screen for that.
 *   - It does not own scroll-to-input on focus — RN's autoscroll on
 *     `keyboardShouldPersistTaps` + `keyboardDismissMode` covers most
 *     cases; if you need precision (e.g. scroll the input above the
 *     keyboard by an exact offset), wire `ScrollView`'s `ref` from
 *     `scrollViewRef` and call `scrollToFocusedInput` yourself.
 */
export interface FormScreenProps {
  children: ReactNode;
  /** Wrap children in a ScrollView. Default: true. Disable for chat-style
   *  layouts where the input bar must stay pinned to the bottom. */
  scrollable?: boolean;
  /** Tap anywhere outside an input dismisses the keyboard. Default: true. */
  dismissOnTap?: boolean;
  /** Horizontal screen padding. Default: 24 (matches existing rider auth). */
  paddingHorizontal?: number;
  /** Minimum bottom padding regardless of safe-area inset. Default: 16. */
  minBottomPadding?: number;
  /** Extra offset above the keyboard (e.g. fixed header height). Default: 0. */
  keyboardVerticalOffset?: number;
  /** ScrollView ref escape hatch for screens that need imperative scroll. */
  scrollViewRef?: React.Ref<ScrollView>;
  /** Override container background. Default: theme `colors.surface`. */
  style?: StyleProp<ViewStyle>;
  /** Inner content container style passthrough for the ScrollView. */
  contentContainerStyle?: StyleProp<ViewStyle>;
  /** Pass through any other ScrollView props (e.g. `onScroll`). */
  scrollViewProps?: Omit<
    ScrollViewProps,
    'contentContainerStyle' | 'keyboardShouldPersistTaps' | 'keyboardDismissMode'
  >;
}

export function FormScreen({
  children,
  scrollable = true,
  dismissOnTap = true,
  paddingHorizontal = 24,
  minBottomPadding = 16,
  keyboardVerticalOffset = 0,
  scrollViewRef,
  style,
  contentContainerStyle,
  scrollViewProps,
}: FormScreenProps) {
  const insets = useSafeAreaInsets();
  const { colors } = useTheme();
  const styles = useMemo(() => createStyles(colors), [colors]);

  const innerPadding = {
    paddingTop: insets.top + 16,
    paddingBottom: Math.max(insets.bottom, minBottomPadding),
    paddingHorizontal,
  };

  const inner = scrollable ? (
    <ScrollView
      ref={scrollViewRef}
      contentContainerStyle={[
        styles.scrollContent,
        innerPadding,
        contentContainerStyle,
      ]}
      keyboardShouldPersistTaps="handled"
      keyboardDismissMode="on-drag"
      showsVerticalScrollIndicator={false}
      {...scrollViewProps}
    >
      {children}
    </ScrollView>
  ) : (
    <View style={[styles.fixedContent, innerPadding]}>{children}</View>
  );

  const tapDismissingInner = dismissOnTap ? (
    <TouchableWithoutFeedback onPress={Keyboard.dismiss} accessible={false}>
      {inner}
    </TouchableWithoutFeedback>
  ) : (
    inner
  );

  // KeyboardAvoidingView pattern:
  //   iOS:     behavior='padding' is the only one that works correctly with
  //            SafeAreaView + ScrollView; 'position' breaks scroll, 'height'
  //            forces a re-layout that fights the keyboard animation.
  //   Android: We deliberately pass `behavior={undefined}` and rely on the
  //            manifest `windowSoftInputMode='adjustResize'` (which the apps
  //            ship with) to do the work. KAV `behavior='height'` on Android
  //            with New Arch / Bridgeless produces inconsistent results
  //            across OEMs (Samsung One UI vs Pixel vs Xiaomi keyboard
  //            insets all differ). adjustResize is the OS-native solution.
  return (
    <KeyboardAvoidingView
      style={[styles.container, style]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      keyboardVerticalOffset={keyboardVerticalOffset}
    >
      {tapDismissingInner}
    </KeyboardAvoidingView>
  );
}

function createStyles(colors: ThemeColors) {
  return StyleSheet.create({
    container: {
      flex: 1,
      backgroundColor: colors.surface,
    },
    scrollContent: {
      flexGrow: 1,
    },
    fixedContent: {
      flex: 1,
    },
  });
}

export default FormScreen;
