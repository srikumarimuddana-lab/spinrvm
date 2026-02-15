import { initializeApp } from 'firebase/app';
import { getAuth, Auth } from 'firebase/auth';

const firebaseConfig = {
  apiKey: process.env.EXPO_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.EXPO_PUBLIC_FIREBASE_APP_ID,
};

// Initialize Firebase
// Check if already initialized to avoid hot reload errors
let app;
let auth: Auth;

if (firebaseConfig.apiKey) {
  try {
    app = initializeApp(firebaseConfig);
  } catch (error: any) {
    // If the app is already initialized, we can ignore this error
    // and let getAuth() use the default app instance.
    if (!/already exists/.test(error.message)) {
      console.error('Firebase initialization error', error.stack);
    }
  }

  try {
    // If app is undefined (e.g. "already exists" error), getAuth() uses the default app.
    // If initialization failed completely, this might throw, so we catch it.
    auth = getAuth(app);
  } catch (error) {
    console.warn('Failed to get Firebase Auth instance, using mock:', error);
    auth = {} as Auth;
  }
} else {
  console.warn('Firebase API key is missing. Skipping initialization.');
  // Mock auth object to prevent build failures when env vars are missing
  auth = {} as Auth;
}

export { auth };
