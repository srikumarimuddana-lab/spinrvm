# Spinr - Rideshare Application

This repository contains a full-stack rideshare application built with React Native (Expo) and Python (FastAPI).

## Project Structure

*   `backend/`: Python FastAPI backend.
    *   `server.py`: Main entry point.
    *   `db.py`: Database abstraction layer (Mongo-like interface).
    *   `db_supabase.py`: Supabase (PostgreSQL) data access logic.
    *   `tests/`: Verification scripts.
*   `frontend/`: React Native (Expo) frontend.
    *   `app/`: Expo Router screens.
    *   `store/`: Zustand state management.
    *   `api/`: API client configuration.
*   `admin-dashboard/`: Admin panel (Next.js/React).

## Deployment

**Spinr uses a hybrid deployment strategy:**

*   **Frontend (Expo Web)**: Deployed to **Vercel** as a Static Site (SPA).
*   **Admin Dashboard (Next.js)**: Deployed to **Vercel** (Serverless).
*   **Backend (Python/FastAPI)**: Deployed to **Render** (Web Service).

See [DEPLOYMENT.md](./DEPLOYMENT.md) for detailed step-by-step instructions.

## Setup Instructions (Local Development)

### Backend

1.  **Prerequisites**: Python 3.8+, Supabase project.
2.  **Environment Variables**: Create a `.env` file in `backend/` based on `.env.example` (if provided) or configure:
    *   `SUPABASE_URL`: Your Supabase URL.
    *   `SUPABASE_SERVICE_ROLE_KEY`: Your Supabase Service Role Key (for backend access).
    *   `JWT_SECRET`: Secret for JWT tokens.
    *   `FIREBASE_SERVICE_ACCOUNT_JSON`: Firebase Admin SDK credentials.
3.  **Install Dependencies**:
    ```bash
    cd backend
    pip install -r requirements.txt
    ```
4.  **Run Server**:
    ```bash
    # From repo root
    python3 -m backend.server
    ```
5.  **Run Tests**:
    ```bash
    python3 -m unittest backend/tests/verify_db.py
    ```

### Frontend

1.  **Prerequisites**: Node.js, Expo CLI.
2.  **Environment Variables**: Create `.env` in `frontend/` with:
    *   `EXPO_PUBLIC_BACKEND_URL`: URL of your running backend (e.g., `http://localhost:8000`).
    *   `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY`: Google Maps API Key.
3.  **Install Dependencies**:
    ```bash
    cd frontend
    npm install
    ```
4.  **Run App**:
    ```bash
    npx expo start
    ```

## Key Features

*   **Rider App**: Book rides, track drivers, rate rides.
*   **Driver App**: Accept rides, navigate to pickup/dropoff, track earnings.
*   **Admin Dashboard**: Manage users, drivers, pricing, and service areas.
*   **Real-time Updates**: WebSocket integration for driver location and ride status.
*   **Geospatial Queries**: PostGIS-powered driver matching and service area checks.

## Architecture

*   **Database**: Supabase (PostgreSQL) with PostGIS extension.
*   **API**: FastAPI (Python) with async endpoints.
*   **Authentication**: Firebase Auth (Phone) + Custom JWT/Supabase Auth integration.
*   **State Management**: Zustand (Frontend).
*   **Maps**: Google Maps Platform (Directions, Places, Maps SDK).

## Notes

*   Ensure Supabase RPC functions (`find_nearby_drivers`, `update_driver_location`, etc.) are created in your database. See `backend/FINAL_SCHEMA.sql` (if available) or migration files.
