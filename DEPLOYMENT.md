# Deployment Guide: Vercel + Railway (or Render)

This guide explains how to deploy the Spinr application using a hybrid approach:
- **Backend (Python/FastAPI)**: Railway (Recommended) or Render
- **Frontend (Expo Web)**: Vercel (Static Site)
- **Admin Dashboard (Next.js)**: Vercel (Next.js App)

## 1. Backend Deployment (Railway - Recommended)

Railway runs the backend from `backend/Dockerfile` with managed Redis/Postgres add-ons
and built-in WebSocket support.

1.  **Install Railway CLI**: `npm i -g @railway/cli` (or use the web dashboard).
2.  **Login**: Run `railway login`.
3.  **Initialize Project**:
    - Run `railway init` from the project root, or create a project in the
      Railway dashboard and link it with `railway link`.
    - Railway will detect `railway.json` and use `backend/Dockerfile` as the
      build source.
    - Choose a service name (e.g., `backend`).
4.  **Set Variables**:
    Set secrets via `railway variables set KEY=VALUE` or in the dashboard:
    ```bash
    railway variables set \
      SUPABASE_URL="your_supabase_url" \
      SUPABASE_KEY="your_supabase_service_role_key" \
      FIREBASE_SERVICE_ACCOUNT_JSON='{"type": "service_account", ...}' \
      JWT_SECRET="your_strong_jwt_secret" \
      RATE_LIMIT_REDIS_URL="$REDIS_URL"
    ```
    *Note: The Firebase JSON must be minified (on one line) or enclosed in single quotes.*
    *Note: `RATE_LIMIT_REDIS_URL` should point at the Railway Redis plugin (or Upstash).*
5.  **Deploy**:
    ```bash
    railway up --service backend
    ```
6.  **Get URL**: Railway assigns a public domain under `*.up.railway.app`.
    Generate or map a custom domain via the dashboard ("Networking" → "Public Domain").

### CI/CD for Railway
A GitHub Action is included in `.github/workflows/deploy-backend.yml`.
1.  Go to your GitHub Repo -> Settings -> Secrets and variables -> Actions.
2.  Add a new secret `RAILWAY_TOKEN`.
    - Generate this token in the Railway dashboard under *Account Settings → Tokens*
      (project-scoped tokens are recommended).
3.  Now, every push to `main` that changes `backend/` will auto-deploy.

---

## Alternative: Backend Deployment (Render)

If you prefer Render:

1.  **Push to GitHub**: Ensure your code is in a GitHub repository.
2.  **Create Render Account**: Go to [render.com](https://render.com).
3.  **New Blueprint**: Click "New +" -> "Blueprint".
4.  **Connect Repo**: Select your `spinr` repository.
5.  **Apply**: Render will read `render.yaml` and create the `spinr-backend` service.
6.  **Environment Variables**: In the Render dashboard for `spinr-backend`, add these secrets:
    - `SUPABASE_URL`: Your Supabase URL.
    - `SUPABASE_KEY`: Your Supabase Service Role Key.
    - `FIREBASE_SERVICE_ACCOUNT_JSON`: The content of your Firebase Admin SDK JSON file.
7.  **Get URL**: Once deployed, copy the service URL (e.g., `https://spinr-backend.onrender.com`).

---

## 2. Frontend Deployment (Vercel)

Deploy the Expo Web app as a static site.

1.  **Create Vercel Account**: Go to [vercel.com](https://vercel.com).
2.  **Install Vercel CLI** (Optional but recommended): `npm i -g vercel`
3.  **Import Project**:
    - Click "Add New..." -> "Project".
    - Import your `spinr` repository.
4.  **Configure Project**:
    - **Root Directory**: `frontend` (Click "Edit" next to Root Directory).
    - **Framework Preset**: Select "Other" or "Vite" (Expo exports static files).
    - **Build Command**: `npx expo export --platform web`
    - **Output Directory**: `dist`
5.  **Environment Variables**:
    Add the following environment variables in Vercel Project Settings:
    - `EXPO_PUBLIC_BACKEND_URL`: Your backend URL (e.g., `https://spinr-backend.up.railway.app`).
    - `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`: Your Google Maps API Key.
    - `EXPO_PUBLIC_FIREBASE_API_KEY`: Your Firebase API Key.
    - `EXPO_PUBLIC_FIREBASE_AUTH_DOMAIN`: Your Firebase Auth Domain.
    - `EXPO_PUBLIC_FIREBASE_PROJECT_ID`: Your Firebase Project ID.
    - `EXPO_PUBLIC_FIREBASE_STORAGE_BUCKET`: Your Firebase Storage Bucket.
    - `EXPO_PUBLIC_FIREBASE_MESSAGING_SENDER_ID`: Your Firebase Messaging Sender ID.
    - `EXPO_PUBLIC_FIREBASE_APP_ID`: Your Firebase App ID.
6.  **Deploy**: Click "Deploy".

**Note**: The `vercel.json` file in `frontend/` handles routing rewrites for the SPA.

## 3. Driver App Deployment (Vercel)

Deploy the **Driver App** (Expo Web) as a static site.

1.  **Import Project**:
    - Go to Vercel Dashboard.
    - Click "Add New..." -> "Project".
    - Import the SAME repository again.
2.  **Configure Project**:
    - **Project Name**: `spinr-driver` (or similar).
    - **Root Directory**: `driver-app` (Click "Edit").
    - **Framework Preset**: Select "Other" (Expo exports static files).
    - **Build Command**: `npx expo export -p web`
    - **Output Directory**: `dist`
3.  **Environment Variables**:
    - Add the same `EXPO_PUBLIC_*` variables as the frontend (see above).
4.  **Deploy**: Click "Deploy".

## 4. Admin Dashboard Deployment (Vercel)

Deploy the Next.js admin panel.

1.  **Import Project**:
    - Go to Vercel Dashboard.
    - Click "Add New..." -> "Project".
    - Import the SAME repository again.
2.  **Configure Project**:
    - **Project Name**: `spinr-admin` (or similar).
    - **Root Directory**: `admin-dashboard` (Click "Edit").
    - **Framework Preset**: Next.js (Should be auto-detected).
3.  **Environment Variables**:
    - Add `NEXT_PUBLIC_API_URL`: The URL of your backend (e.g., `https://spinr-backend.up.railway.app`).
4.  **Deploy**: Click "Deploy".

## Troubleshooting

-   **Firebase Error (auth/invalid-api-key)**:
    - This error means the `EXPO_PUBLIC_FIREBASE_API_KEY` is missing in your Vercel environment variables.
    - Go to Vercel Dashboard -> Project -> Settings -> Environment Variables and ensure all `EXPO_PUBLIC_FIREBASE_*` variables are set correctly.
    - Redeploy (rebuild) the project after adding variables.

-   **CORS Issues**:
    - Ensure your Backend Environment Variable `ALLOWED_ORIGINS` includes your new Vercel domains (e.g., `https://spinr-frontend.vercel.app,https://spinr-admin.vercel.app`).
    - For Railway, verify variables with `railway variables` or the dashboard.

-   **Missing Maps**:
    - If maps don't load, verify `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` is set in Vercel.

-   **WebSockets**:
    - Vercel does not support persistent WebSockets. Ensure your frontend connects to the backend URL (Railway/Render) for WebSocket connections, not the Vercel frontend URL.
