# Setup Keys & Secrets

To run this project, you need to configure several environment variables and secrets.

## Frontend (Expo)

### Local Development (.env)

Create a `.env` file in the `frontend` directory with the following keys:

```env
# Backend URL (Use your machine's IP address if testing on a physical device)
EXPO_PUBLIC_BACKEND_URL=http://localhost:8000

# Firebase Configuration
EXPO_PUBLIC_FIREBASE_API_KEY=your_firebase_api_key
EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN=your_project_id.firebaseapp.com
EXPO_PUBLIC_FIREBASE_PROJECT_ID=your_project_id
EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET=your_project_id.appspot.com
EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID=your_messaging_sender_id
EXPO_PUBLIC_FIREBASE_APP_ID=your_app_id

# Google Maps (For MapView)
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=your_google_maps_api_key

# Stripe (For Payments)
EXPO_PUBLIC_STRIPE_PUBLISHABLE_KEY=pk_test_...
```

### Production (EAS Build)

When building with EAS, you must set these secrets in your Expo project dashboard or via CLI:

```bash
eas secret:create --scope project --name EXPO_PUBLIC_FIREBASE_API_KEY --value "your_key"
eas secret:create --scope project --name EXPO_PUBLIC_BACKEND_URL --value "https://your-api.com"
# ... repeat for all keys above
```

## Backend (FastAPI)

### Local Development (.env)

Create a `.env` file in the `backend` directory (or root):

```env
# Database (Supabase)
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_service_role_key

# Firebase Admin SDK (For verifying tokens)
# Option 1: Path to service account JSON file
GOOGLE_APPLICATION_CREDENTIALS=path/to/firebase-service-account.json

# Option 2: JSON string content (Useful for cloud deployment env vars)
FIREBASE_SERVICE_ACCOUNT_JSON={"type": "service_account", ...}

# Stripe Secret Key
STRIPE_SECRET_KEY=sk_test_...

# JWT Secret (Legacy / Internal)
JWT_SECRET=your_jwt_secret
```

### Production (Render/Heroku/Docker)

Set these environment variables in your deployment platform's dashboard.

## Services Setup

1.  **Firebase:**
    *   Create a project in [Firebase Console](https://console.firebase.google.com/).
    *   Enable **Authentication** -> **Phone Sign-in**.
    *   Add Android/iOS apps to get `google-services.json` / `GoogleService-Info.plist` (Download and place in `frontend/` root, update `app.json` to reference them if using native build, though for Expo Go purely JS config works for web-based auth flow, but for native phone auth you need the native files).
    *   *Note: This project uses JS SDK for Auth, which works in Expo Go.*

2.  **Supabase:**
    *   Create a project.
    *   Run the SQL migrations in `backend/FINAL_SCHEMA.sql` via the SQL Editor.
    *   Enable PostGIS extension.

3.  **Google Maps:**
    *   Enable Maps SDK for Android and Maps SDK for iOS in Google Cloud Console.
    *   Get an API Key.

4.  **Stripe:**
    *   Get test keys from Stripe Dashboard.
