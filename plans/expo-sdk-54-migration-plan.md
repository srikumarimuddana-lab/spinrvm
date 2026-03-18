# Expo SDK 54 Migration Plan

## Overview
Downgrade both rider-app and driver-app from Expo SDK 55 to SDK 54 for compatibility reasons.

## Current State
- **rider-app**: Expo SDK 55.0.0, React Native 0.83.2
- **driver-app**: Expo SDK 55.0.0, React Native 0.83.2

## Target State
- **rider-app**: Expo SDK 54.0.0, React Native 0.76.x
- **driver-app**: Expo SDK 54.0.0, React Native 0.76.x

---

## Rider-App Package.json Changes

### Core Expo Packages (55 → 54)
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| expo | ~55.0.0 | ~54.0.0 |
| expo-blur | ~55.0.9 | ~54.0.0 |
| expo-clipboard | ~55.0.8 | ~54.0.0 |
| expo-constants | ~55.0.7 | ~54.0.0 |
| expo-document-picker | ~55.0.8 | ~54.0.0 |
| expo-font | ~55.0.4 | ~54.0.0 |
| expo-haptics | ~55.0.8 | ~54.0.0 |
| expo-image | ~55.0.6 | ~54.0.0 |
| expo-image-picker | ~55.0.12 | ~54.0.0 |
| expo-linear-gradient | ~55.0.8 | ~54.0.0 |
| expo-linking | ~55.0.7 | ~54.0.0 |
| expo-location | ~55.1.2 | ~54.0.0 |
| expo-router | ~55.0.5 | ~54.0.0 |
| expo-secure-store | ~55.0.8 | ~54.0.0 |
| expo-splash-screen | ~55.0.10 | ~54.0.0 |
| expo-status-bar | ~55.0.4 | ~54.0.0 |
| expo-symbols | ~55.0.5 | ~54.0.0 |
| expo-system-ui | ~55.0.9 | ~54.0.0 |
| expo-web-browser | ~55.0.9 | ~54.0.0 |

### React Native & Core Packages (Major Changes)
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| react | 19.2.0 | 18.3.1 |
| react-dom | 19.2.0 | 18.3.1 |
| react-native | 0.83.2 | 0.76.9 |
| react-native-reanimated | ^4.2.1 | ~3.16.0 |
| react-native-worklets-core | 1.6.3 | Remove (not needed with reanimated 3) |
| react-native-gesture-handler | ~2.30.0 | ~2.20.0 |
| react-native-safe-area-context | ~5.6.0 | ~4.14.0 |
| react-native-screens | ~4.23.0 | ~4.4.0 |
| react-native-web | ^0.21.0 | ~0.19.0 |

### Navigation Packages
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| @react-navigation/bottom-tabs | ^7.3.10 | ^7.0.0 |
| @react-navigation/elements | ^2.3.8 | ^2.0.0 |
| @react-navigation/native | ^7.1.6 | ^7.0.0 |

### Other Dependencies
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| @react-native-async-storage/async-storage | 2.2.0 | 2.1.0 |
| @react-native-community/datetimepicker | 8.6.0 | 8.0.0 |
| @stripe/stripe-react-native | 0.58.0 | 0.38.0 |
| react-native-maps | 1.26.20 | 1.18.0 |
| zustand | ^5.0.11 | ^5.0.0 |

### DevDependencies
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| eslint-config-expo | ~55.0.0 | ~54.0.0 |
| typescript | ~5.9.2 | ~5.3.0 |

---

## Driver-App Package.json Changes

### Core Expo Packages (55 → 54)
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| expo | ~55.0.0 | ~54.0.0 |
| expo-blur | ~55.0.9 | ~54.0.0 |
| expo-clipboard | ~55.0.8 | ~54.0.0 |
| expo-constants | ~55.0.7 | ~54.0.0 |
| expo-document-picker | ~55.0.8 | ~54.0.0 |
| expo-font | ~55.0.4 | ~54.0.0 |
| expo-haptics | ~55.0.8 | ~54.0.0 |
| expo-image | ~55.0.6 | ~54.0.0 |
| expo-image-picker | ~55.0.12 | ~54.0.0 |
| expo-linear-gradient | ~55.0.8 | ~54.0.0 |
| expo-linking | ~55.0.7 | ~54.0.0 |
| expo-location | ~55.1.2 | ~54.0.0 |
| expo-notifications | ~55.0.0 | ~54.0.0 |
| expo-router | ~55.0.5 | ~54.0.0 |
| expo-secure-store | ~55.0.8 | ~54.0.0 |
| expo-splash-screen | ~55.0.10 | ~54.0.0 |
| expo-status-bar | ~55.0.4 | ~54.0.0 |
| expo-symbols | ~55.0.5 | ~54.0.0 |
| expo-system-ui | ~55.0.9 | ~54.0.0 |
| expo-web-browser | ~55.0.9 | ~54.0.0 |

### React Native & Core Packages (Major Changes)
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| react | 19.2.0 | 18.3.1 |
| react-dom | 19.2.0 | 18.3.1 |
| react-native | 0.83.2 | 0.76.9 |
| react-native-reanimated | ~4.2.1 | ~3.16.0 |
| react-native-worklets | 0.7.1 | Remove (not needed with reanimated 3) |
| react-native-gesture-handler | ~2.30.0 | ~2.20.0 |
| react-native-safe-area-context | ~5.6.0 | ~4.14.0 |
| react-native-screens | ~4.23.0 | ~4.4.0 |
| react-native-web | ^0.21.0 | ~0.19.0 |

### Navigation Packages
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| @react-navigation/bottom-tabs | ^7.3.10 | ^7.0.0 |
| @react-navigation/elements | ^2.3.8 | ^2.0.0 |
| @react-navigation/native | ^7.1.6 | ^7.0.0 |

### Other Dependencies
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| @react-native-async-storage/async-storage | 2.2.0 | 2.1.0 |
| @react-native-community/datetimepicker | 8.6.0 | 8.0.0 |
| @stripe/stripe-react-native | 0.58.0 | 0.38.0 |
| react-native-maps | 1.26.20 | 1.18.0 |
| zustand | ^5.0.11 | ^5.0.0 |

### DevDependencies
| Package | Current Version | New Version |
|---------|-----------------|-------------|
| eslint-config-expo | ~55.0.0 | ~54.0.0 |
| typescript | ~5.9.2 | ~5.3.0 |

---

## App.config.ts Changes

### rider-app/app.config.ts
- No major changes required for SDK 54
- `newArchEnabled: true` should remain (SDK 54 supports New Architecture)
- Verify all plugin versions are compatible

### driver-app/app.config.ts
- No major changes required for SDK 54
- `newArchEnabled: true` should remain (SDK 54 supports New Architecture)
- Verify all plugin versions are compatible

---

## Migration Steps

### Step 1: Update rider-app/package.json
1. Update all expo-* packages to ~54.0.0
2. Update React Native to 0.76.9
3. Update react and react-dom to 18.3.1
4. Update react-native-reanimated to ~3.16.0
5. Remove react-native-worklets-core
6. Update all other dependency versions as specified

### Step 2: Update driver-app/package.json
1. Update all expo-* packages to ~54.0.0
2. Update React Native to 0.76.9
3. Update react and react-dom to 18.3.1
4. Update react-native-reanimated to ~3.16.0
5. Remove react-native-worklets
6. Update all other dependency versions as specified

### Step 3: Install Dependencies
```bash
# For rider-app
cd rider-app
rm -rf node_modules package-lock.json
yarn install

# For driver-app
cd driver-app
rm -rf node_modules package-lock.json
yarn install
```

### Step 4: Test Both Apps
- Test rider-app on iOS and Android
- Test driver-app on iOS and Android
- Verify all features work correctly
- Check for any runtime errors

---

## Breaking Changes to Watch For

1. **React 18 vs React 19**: Some component patterns may need updating
2. **Reanimated 3 vs 4**: Animation configurations may differ
3. **New Architecture**: Verify all native modules work with New Architecture on SDK 54
4. **Navigation**: React Navigation 7.x may have some API changes
5. **Maps**: react-native-maps 1.18.0 has some API changes from 1.26.x

---

## Notes
- SDK 54 uses React Native 0.76.x which is more stable than 0.83.x
- Reanimated 3 is required for SDK 54 (not Reanimated 4)
- The `react-native-worklets` packages are only needed for Reanimated 4
- After migration, run `expo prebuild` to regenerate native code
