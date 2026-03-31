# Expo SDK 52 Downgrade Specification

## Current State Analysis

| App | Current Expo SDK | React Version | React Native Version | Status |
|-----|------------------|---------------|----------------------|--------|
| frontend | ~55.0.0 | 19.2.0 | 0.83.2 | Needs downgrade |
| driver-app | ~52.0.0 | 18.3.1 | 0.76.9 | Already at SDK 52 |
| rider-app | ~54.0.0 | 18.3.1 | 0.76.9 | Needs downgrade |

## Target State
All apps should use Expo SDK 52 with compatible dependencies, matching the driver-app configuration.

## Detailed Changes Required

### 1. frontend/package.json Changes

#### Expo Core Packages (Change from ~55.x to ~52.x)
- expo: ~55.0.0 → ~52.0.0
- expo-blur: ~55.0.10 → ~52.0.10
- expo-clipboard: ~55.0.9 → ~52.0.9
- expo-constants: ~55.0.9 → ~52.0.9
- expo-document-picker: ~55.0.9 → ~52.0.9
- expo-font: ~55.0.4 → ~52.0.4
- expo-haptics: ~55.0.9 → ~52.0.9
- expo-image: ~55.0.6 → ~52.0.6
- expo-image-picker: ~55.0.13 → ~52.0.13
- expo-linear-gradient: ~55.0.9 → ~52.0.9
- expo-linking: ~55.0.8 → ~52.0.8
- expo-location: ~55.1.4 → ~52.1.4
- expo-router: ~55.0.7 → ~4.0.0 (Note: SDK 52 uses expo-router v4)
- expo-secure-store: ~55.0.9 → ~52.0.9
- expo-splash-screen: ~55.0.12 → ~52.0.12
- expo-status-bar: ~55.0.4 → ~52.0.4
- expo-symbols: ~55.0.5 → ~52.0.5
- expo-system-ui: ~55.0.10 → ~52.0.10
- expo-web-browser: ~55.0.10 → ~52.0.10

#### React and React Native (Downgrade to SDK 52 compatible versions)
- react: 19.2.0 → 18.3.1
- react-dom: 19.2.0 → 18.3.1
- react-native: 0.83.2 → 0.76.9

#### Other Dependencies (Adjust for compatibility)
- @expo/vector-icons: ^15.0.3 → ^14.0.0
- @react-native-async-storage/async-storage: 2.2.0 → 2.1.0
- @react-native-community/netinfo: ^11.4.1 → ^12.0.1
- @react-navigation/bottom-tabs: ^7.3.10 → ^7.0.0
- @react-navigation/elements: ^2.3.8 → ^2.0.0
- @react-navigation/native: ^7.1.6 → ^7.0.0
- @stripe/stripe-react-native: 0.58.0 → 0.38.0
- firebase: ^12.9.0 → ^10.8.0
- react-native-gesture-handler: ~2.30.0 → ~2.20.0
- react-native-maps: 1.27.2 → 1.18.0
- react-native-reanimated: 4.2.1 → ~3.16.1
- react-native-safe-area-context: ~5.6.0 → ~5.0.0
- react-native-screens: ~4.23.0 → ~4.4.0
- react-native-web: ^0.21.0 → ~0.19.0
- react-native-webview: 13.16.0 → 13.12.0

#### DevDependencies
- @types/react: ~19.2.10 → ~18.3.0
- eslint-config-expo: ~55.0.0 → ~52.0.0
- typescript: ~5.9.2 → ~5.3.3

#### Overrides and Resolutions
- @types/react: ~19.2.10 → ~18.3.0

### 2. rider-app/package.json Changes

#### Expo Core Packages (Change from ~54.x to ~52.x)
- expo: ~54.0.0 → ~52.0.0
- expo-blur: ~54.0.0 → ~52.0.10
- expo-clipboard: ~54.0.0 → ~52.0.9
- expo-constants: ~54.0.0 → ~52.0.9
- expo-document-picker: ~54.0.0 → ~52.0.9
- expo-font: ~54.0.0 → ~52.0.4
- expo-haptics: ~54.0.0 → ~52.0.9
- expo-image: ~54.0.0 → ~52.0.6
- expo-image-picker: ~54.0.0 → ~52.0.13
- expo-linear-gradient: ~54.0.0 → ~52.0.9
- expo-linking: ~54.0.0 → ~52.0.8
- expo-location: ~54.0.0 → ~52.1.4
- expo-router: ~54.0.0 → ~4.0.0
- expo-secure-store: ~54.0.0 → ~52.0.9
- expo-splash-screen: ~54.0.0 → ~52.0.12
- expo-status-bar: ~54.0.0 → ~52.0.4
- expo-symbols: ~54.0.0 → ~52.0.5
- expo-system-ui: ~54.0.0 → ~52.0.10
- expo-web-browser: ~54.0.0 → ~52.0.10

#### Other Dependencies (Adjust for compatibility)
- @react-native-community/netinfo: Not present, add if needed
- @react-navigation/bottom-tabs: ^7.0.0 (already compatible)
- @react-navigation/elements: ^2.0.0 (already compatible)
- @react-navigation/native: ^7.0.0 (already compatible)
- @stripe/stripe-react-native: 0.38.0 (already compatible)
- react-native-safe-area-context: ~4.14.0 → ~5.0.0
- react-native-reanimated: 4.2.1 → ~3.16.1

#### DevDependencies
- eslint-config-expo: ~54.0.0 → ~52.0.0

### 3. driver-app/package.json
No changes required - already at SDK 52.

## Implementation Steps

1. **Backup current package.json files** (optional but recommended)
2. **Update frontend/package.json** with all changes listed above
3. **Update rider-app/package.json** with all changes listed above
4. **Run npm install or yarn install** in each directory to update lock files
5. **Test each app** to ensure compatibility

## Potential Issues and Mitigations

1. **Breaking Changes in React 19 → 18**: Some React 19 features may not be available. Review code for React 19 specific APIs.
2. **Expo Router Version Change**: expo-router v5 (SDK 55) to v4 (SDK 52) may have API differences. Review routing code.
3. **Firebase Version Downgrade**: Firebase v12 to v10 may have breaking changes. Review Firebase usage.
4. **Stripe React Native Version**: Major version downgrade (0.58.0 → 0.38.0) may have breaking changes. Review payment integration.

## Verification Checklist

- [ ] All Expo packages updated to SDK 52 versions
- [ ] React and React Native versions match SDK 52 requirements
- [ ] All dependencies are compatible with SDK 52
- [ ] Lock files regenerated successfully
- [ ] Apps build without errors
- [ ] Apps run without runtime errors
- [ ] Core functionality tested (navigation, maps, payments, etc.)
