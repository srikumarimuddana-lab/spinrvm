# Deployment Guide: Vercel + Render Hybrid

This guide explains how to deploy the Spinr application using a hybrid approach:
- **Frontend (Expo Web)**: Vercel (Static Site)
- **Admin Dashboard (Next.js)**: Vercel (Next.js App)
- **Backend (Python/FastAPI)**: Render (Web Service)

## 1. Backend Deployment (Render)

The backend must be deployed first so you have the URL to provide to the frontend.

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
    - Add `EXPO_PUBLIC_BACKEND_URL`: The URL of your Render backend (e.g., `https://spinr-backend.onrender.com`).
    - Add `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`: Your Google Maps API Key.
6.  **Deploy**: Click "Deploy".

**Note**: The `vercel.json` file in `frontend/` handles routing rewrites for the SPA.

## 3. Admin Dashboard Deployment (Vercel)

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
    - Add `NEXT_PUBLIC_API_URL`: The URL of your Render backend (e.g., `https://spinr-backend.onrender.com`).
4.  **Deploy**: Click "Deploy".

## Troubleshooting

-   **CORS Issues**: Ensure your Backend Environment Variable `ALLOWED_ORIGINS` includes your new Vercel domains (e.g., `https://spinr-frontend.vercel.app,https://spinr-admin.vercel.app`).
-   **Missing API Key**: If maps don't load, verify `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` is set in Vercel.
-   **WebSockets**: Vercel does not support persistent WebSockets, which is why the backend remains on Render.
