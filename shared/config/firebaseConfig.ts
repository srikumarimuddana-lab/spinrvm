import { initializeApp, getApps } from 'firebase/app';
import { getAuth, Auth } from 'firebase/auth';

const firebaseConfig = {
  apiKey: process.env.EXPO_PUBLIC_FIREBASE_API_KEY || "AIzaSyByTO6NI8BAxe7x478-CBkVIhDHkyRlGLI",
  authDomain: process.env.EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN || "spinrapp-6e464.firebaseapp.com",
  projectId: process.env.EXPO_PUBLIC_FIREBASE_PROJECT_ID || "spinrapp-6e464",
  storageBucket: process.env.EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET || "spinrapp-6e464.firebasestorage.app",
  messagingSenderId: process.env.EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID || "879808882715",
  appId: process.env.EXPO_PUBLIC_FIREBASE_APP_ID || "1:879808882715:web:83c466424c80fcefcb30be",
  measurementId: process.env.EXPO_PUBLIC_FIREBASE_MEASUREMENT_ID || "G-QX0JQ6DH53",
};

// Initialize Firebase (avoid duplicate init on hot reload)
let app;
let auth: Auth;

try {
  app = getApps().length === 0 ? initializeApp(firebaseConfig) : getApps()[0];
  auth = getAuth(app);
} catch (error: any) {
  console.warn('Firebase init error:', error.message);
  auth = {} as Auth;
}

export { app, auth, firebaseConfig };
