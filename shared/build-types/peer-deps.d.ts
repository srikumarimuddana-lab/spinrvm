/**
 * Ambient module stubs for peer dependencies that are not installed in
 * shared/node_modules. These packages are provided by the consuming apps
 * (rider-app, driver-app, admin-dashboard) at runtime.
 *
 * These stubs allow `tsc --project tsconfig.build.json` to produce clean
 * declaration files without pulling every RN/Expo package into shared's
 * own devDependencies.
 */

declare module 'firebase/app' {
  export function initializeApp(config: Record<string, string>): unknown;
  export function getApps(): Array<{ name: string }>;
}

declare module 'firebase/auth' {
  export interface User {
    uid: string;
    phoneNumber: string | null;
    getIdToken(forceRefresh?: boolean): Promise<string>;
  }
  export type FirebaseUser = User;
  export interface Auth {
    onAuthStateChanged: (cb: (user: User | null) => void) => () => void;
    currentUser: User | null;
  }
  export function getAuth(app?: unknown): Auth;
  export class PhoneAuthProvider {
    static credential(verificationId: string, verificationCode: string): unknown;
    verifyPhoneNumber(phoneNumber: string): Promise<string>;
  }
  export function signInWithCredential(auth: Auth, credential: unknown): Promise<{ user: User }>;
  export function signOut(auth: Auth): Promise<void>;
}

declare module '@react-native-firebase/messaging' {
  export namespace FirebaseMessagingTypes {
    interface RemoteMessage {
      data?: Record<string, string>;
      notification?: { title?: string; body?: string };
      messageId?: string;
    }
    interface Statics {
      AuthorizationStatus: { AUTHORIZED: number; PROVISIONAL: number; DENIED: number; NOT_DETERMINED: number };
    }
  }
  interface MessagingInstance {
    onMessage: (cb: (msg: FirebaseMessagingTypes.RemoteMessage) => Promise<unknown> | unknown) => () => void;
    onNotificationOpenedApp: (cb: (msg: FirebaseMessagingTypes.RemoteMessage) => void) => () => void;
    getInitialNotification: () => Promise<FirebaseMessagingTypes.RemoteMessage | null>;
    getToken: () => Promise<string>;
    requestPermission: () => Promise<number>;
    AuthorizationStatus: { AUTHORIZED: number; PROVISIONAL: number; DENIED: number; NOT_DETERMINED: number };
    setBackgroundMessageHandler: (cb: (msg: FirebaseMessagingTypes.RemoteMessage) => Promise<unknown>) => void;
    onTokenRefresh: (cb: (token: string) => void) => () => void;
  }
  const messaging: (() => MessagingInstance) & FirebaseMessagingTypes.Statics;
  export default messaging;
}

declare module '@react-native-firebase/crashlytics' {
  const crashlytics: () => {
    recordError: (e: Error) => void;
    log: (msg: string) => void;
    setAttribute: (key: string, value: string) => void;
    setCrashlyticsCollectionEnabled: (enabled: boolean) => Promise<null>;
    setUserId: (userId: string) => Promise<null>;
  };
  export default crashlytics;
}

declare module '@react-native-firebase/app-check' {
  const appCheck: () => {
    initializeAppCheck: (opts: unknown) => void;
    getToken: (forceRefresh?: boolean) => Promise<{ token: string }>;
    newReactNativeFirebaseAppCheckProvider: () => unknown;
  };
  export default appCheck;
}

declare module 'zustand' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type AnyFn = (...args: any[]) => any;
  type SetState<T> = (partial: Partial<T> | ((state: T) => Partial<T>)) => void;
  type GetState<T> = () => T;
  type StoreApi<T> = {
    setState: SetState<T>;
    getState: GetState<T>;
    subscribe: (cb: (state: T, prev: T) => void) => () => void;
    destroy: () => void;
  };
  type StateCreator<T> = (set: SetState<T>, get: GetState<T>, api: StoreApi<T>) => T;
  type UseBoundStore<T> = {
    (): T;
    <U>(selector: (state: T) => U): U;
    getState: GetState<T>;
    setState: SetState<T>;
    subscribe: (cb: (state: T, prev: T) => void) => () => void;
  };
  // Direct form: create<T>(initializer)
  export function create<T>(initializer: StateCreator<T>): UseBoundStore<T>;
  // Curried form used with middleware: create<T>()(persist(...))
  export function create<T>(): (initializer: AnyFn) => UseBoundStore<T>;
}

declare module 'zustand/middleware' {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  type AnyFn = (...args: any[]) => any;
  export function persist<T>(
    config: AnyFn,
    options: { name: string; storage?: unknown }
  ): AnyFn;
  export function createJSONStorage(factory: () => unknown): unknown;
}

declare module 'expo-secure-store' {
  export function getItemAsync(key: string): Promise<string | null>;
  export function setItemAsync(key: string, value: string): Promise<void>;
  export function deleteItemAsync(key: string): Promise<void>;
}

declare module 'expo-constants' {
  interface ExpoConfig {
    extra?: Record<string, string | undefined>;
    name: string;
    hostUri?: string;
    [key: string]: unknown;
  }
  interface Constants {
    expoConfig: ExpoConfig | null;
    manifest?: ExpoConfig;
    hostUri?: string;
  }
  const Constants: Constants;
  export default Constants;
}

declare module 'expo-router' {
  export function useRouter(): {
    push: (href: string) => void;
    replace: (href: string) => void;
    back: () => void;
    canGoBack: () => boolean;
  };
  export function useLocalSearchParams<T extends Record<string, string>>(): T;
  export function usePathname(): string;
}

declare module 'expo-location' {
  export interface LocationObject {
    coords: { latitude: number; longitude: number; accuracy: number | null };
    timestamp: number;
  }
  export enum Accuracy {
    Lowest = 1,
    Low = 2,
    Balanced = 3,
    High = 4,
    Highest = 5,
    BestForNavigation = 6,
  }
  export function requestForegroundPermissionsAsync(): Promise<{ status: string }>;
  export function getCurrentPositionAsync(opts?: { accuracy?: Accuracy }): Promise<LocationObject>;
  export function watchPositionAsync(
    opts: unknown,
    cb: (loc: LocationObject) => void
  ): Promise<{ remove: () => void }>;
}

declare module '@expo/vector-icons' {
  import { ComponentProps } from 'react';
  import { TextStyle } from 'react-native';
  interface IconProps {
    name: string;
    size?: number;
    color?: string;
    style?: TextStyle;
  }
  export class Ionicons extends React.Component<IconProps> {}
  export class MaterialIcons extends React.Component<IconProps> {}
  export class FontAwesome extends React.Component<IconProps> {}
  export class AntDesign extends React.Component<IconProps> {}
}

declare module 'react-native-maps' {
  import { Component } from 'react';
  import { ViewStyle } from 'react-native';
  export interface LatLng { latitude: number; longitude: number }
  export interface Region extends LatLng { latitudeDelta: number; longitudeDelta: number }
  interface MapViewProps { style?: ViewStyle; region?: Region; [key: string]: unknown }
  interface MarkerProps { coordinate: LatLng; identifier?: string; [key: string]: unknown }
  export class Marker extends Component<MarkerProps> {}
  export const PROVIDER_GOOGLE: string;
  export const PROVIDER_DEFAULT: string;
  export default class MapView extends Component<MapViewProps> {}
}

declare module 'react-native-safe-area-context' {
  import { Component, FC } from 'react';
  import { ViewStyle, ViewProps } from 'react-native';
  export interface SafeAreaViewProps extends ViewProps { style?: ViewStyle }
  export const SafeAreaView: FC<SafeAreaViewProps>;
  export function useSafeAreaInsets(): { top: number; bottom: number; left: number; right: number };
}

declare module 'react-native-url-polyfill/auto' {}

declare module '@react-native-async-storage/async-storage' {
  const AsyncStorage: {
    getItem: (key: string) => Promise<string | null>;
    setItem: (key: string, value: string) => Promise<void>;
    removeItem: (key: string) => Promise<void>;
    multiGet: (keys: string[]) => Promise<Array<[string, string | null]>>;
    multiSet: (pairs: Array<[string, string]>) => Promise<void>;
  };
  export default AsyncStorage;
}

declare module '@react-native-community/netinfo' {
  export type NetInfoState = {
    isConnected: boolean | null;
    isInternetReachable: boolean | null;
    type: string;
  };
  export type NetInfoSubscription = () => void;
  export function addEventListener(
    listener: (state: NetInfoState) => void
  ): NetInfoSubscription;
  export function fetch(): Promise<NetInfoState>;
}

declare module '@tanstack/react-query' {
  export interface QueryClient {
    invalidateQueries: (opts?: unknown) => Promise<void>;
    prefetchQuery: (opts: unknown) => Promise<void>;
    getQueryData: <T>(key: readonly unknown[]) => T | undefined;
  }
  export function useQuery<T>(opts: {
    queryKey: readonly unknown[];
    queryFn: () => Promise<T>;
    enabled?: boolean;
    staleTime?: number;
    gcTime?: number;
    retry?: boolean | number;
    refetchOnWindowFocus?: boolean;
    refetchOnReconnect?: boolean;
    [key: string]: unknown;
  }): { data: T | undefined; isLoading: boolean; isPending: boolean; error: unknown; refetch: () => void };
  export function useMutation<T, V = void>(opts: {
    mutationFn: (vars: V) => Promise<T>;
    onSuccess?: (data: T, vars: V) => void;
    onError?: (err: unknown, vars: V) => void;
    onSettled?: (data: T | undefined, err: unknown, vars: V) => void;
  }): { mutate: (vars: V) => void; mutateAsync: (vars: V) => Promise<T>; isPending: boolean; isError: boolean; error: unknown };
  export function useQueryClient(): QueryClient;
  export class QueryClient {
    constructor(opts?: unknown);
    invalidateQueries(opts?: unknown): Promise<void>;
  }
}

declare module '@tanstack/query-async-storage-persister' {
  export function createAsyncStoragePersister(opts: unknown): unknown;
}

declare module '@supabase/supabase-js' {
  export function createClient(url: string, key: string, opts?: unknown): unknown;
  export type SupabaseClient = ReturnType<typeof createClient>;
}

