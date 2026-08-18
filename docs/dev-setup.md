# Spinr — Local Development Setup

This guide gets all four Spinr surfaces running locally and connects physical devices for testing. It covers first-time setup only; for a full variable reference see [ENVIRONMENT_VARIABLES.md](./ENVIRONMENT_VARIABLES.md).

---

## Prerequisites

| Tool | Required version | Install |
|------|-----------------|---------|
| Python | 3.12+ | [python.org](https://python.org) |
| Node.js | 18+ (LTS) | [nodejs.org](https://nodejs.org) |
| npm | 9+ | bundled with Node |
| yarn | 1.22+ | `npm install -g yarn` |
| Expo CLI | latest | `npm install -g expo-cli` |
| Expo Go app | latest | App Store / Google Play on test devices |
| Git | any | [git-scm.com](https://git-scm.com) |

You need credentials for:
- **Supabase** project (URL + service role key)
- **Google Maps API** key (Maps SDK for Android + iOS enabled)
- **Firebase** service account JSON (for push notifications — optional in dev)

---

## 1. Clone and Install Dependencies

```bash
git clone https://github.com/your-org/spinrvm.git
cd spinrvm
```

Install each workspace:

```bash
# Backend
cd backend && pip install -r requirements.txt && cd ..

# Rider app
cd rider-app && yarn install && cd ..

# Driver app
cd driver-app && yarn install && cd ..

# Admin dashboard
cd admin-dashboard && npm ci && cd ..
```

---

## 2. Create `.env` Files

Three files need to be created manually. **They are gitignored — never commit them.**

### 2a. `backend/.env`

Copy the example then fill in your credentials:

```bash
cp backend/.env.example backend/.env
```

Minimum viable local config:

```dotenv
SUPABASE_URL=https://<your-project-ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=<service_role_key_from_supabase_dashboard>
JWT_SECRET=<any-random-32-char-string>
ENV=development
```

How to get each value:

| Variable | Where to find it |
|----------|-----------------|
| `SUPABASE_URL` | Supabase Dashboard → Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase Dashboard → Settings → API → `service_role` key |
| `JWT_SECRET` | Run `openssl rand -hex 32` (or any 32+ char string in dev) |

Optional but useful in dev:

```dotenv
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8081,http://localhost:19006
```

> `FIREBASE_SERVICE_ACCOUNT_JSON` must be the full JSON content as a single-line string (no newlines). Firebase push notifications are skipped gracefully when this is absent.

> In `ENV=development`, the OTP bypass code `"1234"` is active so you don't need a live Twilio account to test login.

### 2b. `rider-app/.env`

```bash
cp rider-app/.env.example rider-app/.env
```

```dotenv
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<your_google_maps_api_key>
```

For **physical device testing** (Expo Go on a phone), add:

```dotenv
EXPO_PUBLIC_BACKEND_URL=http://<your-lan-ip>:8000
```

See [section 5](#5-connect-physical-devices) for how to find your LAN IP.

### 2c. `driver-app/.env`

```bash
cp driver-app/.env.example driver-app/.env
```

Same as rider-app:

```dotenv
EXPO_PUBLIC_GOOGLE_MAPS_API_KEY=<your_google_maps_api_key>
EXPO_PUBLIC_BACKEND_URL=http://<your-lan-ip>:8000
```

> The rider and driver apps can share the same Google Maps API key in development. For production, use separate keys restricted to each app's bundle ID.

---

## 3. Start the Backend

Run from the **repo root** (not from inside `backend/`):

```bash
python3 -m backend.server
```

The API starts on `http://localhost:8000`. Verify it's up:

```bash
curl http://localhost:8000/health
# → {"status":"ok"}
```

> The backend reads `backend/.env` automatically via pydantic-settings. If you see `ValidationError: JWT_SECRET` the `.env` file is missing or not in the right directory.

### Apply database migrations (first run only)

```bash
cd backend
python -m backend.scripts.run_migrations
cd ..
```

This runs all ordered SQL files in `backend/migrations/` against your Supabase project.

---

## 4. Start the Admin Dashboard

```bash
cd admin-dashboard
npm run dev
```

Opens at `http://localhost:3000`. The dashboard is pre-configured — its `.env.local` already points to `http://localhost:8000`.

---

## 5. Start the Mobile Apps

Each app runs as an Expo dev server. Start them in separate terminals.

**Rider app:**

```bash
cd rider-app
yarn start
```

**Driver app:**

```bash
cd driver-app
yarn start
```

Both commands print a QR code. Scan it with **Expo Go** on an iOS or Android device to open the app.

---

## 6. Connect Physical Devices

Expo Go connects to your dev server over the local network. Both your computer and the test device must be on the **same Wi-Fi network**.

### Find your LAN IP

**macOS / Linux:**

```bash
ipconfig getifaddr en0       # Wi-Fi interface (macOS)
ip route get 1 | awk '{print $7;exit}'  # Linux
```

**Windows:**

```powershell
ipconfig | findstr "IPv4"
```

Use the `192.168.x.x` or `10.x.x.x` address shown.

### Update `.env` files

Set `EXPO_PUBLIC_BACKEND_URL` in both mobile app `.env` files:

```dotenv
EXPO_PUBLIC_BACKEND_URL=http://192.168.1.42:8000
```

Restart the Expo dev servers after changing `.env`:

```bash
# In the rider-app terminal:
yarn start --clear

# In the driver-app terminal:
yarn start --clear
```

### Verify device connectivity

On the physical device, open a browser and navigate to:

```
http://<your-lan-ip>:8000/health
```

If you see `{"status":"ok"}`, the device can reach the backend.

> **Windows Firewall**: If the device can't reach the backend, allow inbound traffic on port 8000 in Windows Defender Firewall → Inbound Rules → New Rule → Port 8000 → Allow.

---

## 7. Verify the Full Stack

Once everything is running, do a quick sanity check:

| Check | Expected |
|-------|----------|
| `curl http://localhost:8000/health` | `{"status":"ok"}` |
| Admin dashboard at `localhost:3000` | Login page loads, no 502 errors |
| Expo Go — rider app | Map screen loads (requires Maps API key) |
| Expo Go — driver app | Map screen loads |
| Backend terminal | No import errors on startup |

For a deeper smoke test, run the backend unit tests:

```bash
cd backend
pytest -m "not slow"
```

---

## 8. Troubleshooting

**Backend won't start — `ValidationError: JWT_SECRET`**
→ `backend/.env` is missing or in the wrong directory. The file must be at `backend/.env` (relative to repo root).

**`ModuleNotFoundError: No module named 'backend'`**
→ Run the server from the **repo root**, not from inside `backend/`: `python3 -m backend.server`

**Expo Go shows "Network request failed"**
→ `EXPO_PUBLIC_BACKEND_URL` points to `localhost` instead of your LAN IP, or the firewall is blocking port 8000.

**Maps don't load on the device**
→ `EXPO_PUBLIC_GOOGLE_MAPS_API_KEY` is missing from the mobile app `.env`, or the key doesn't have Maps SDK for Android/iOS enabled in Google Cloud Console.

**OTP login not working locally**
→ In `ENV=development`, use OTP code `"1234"` — Twilio SMS is not required.

**Supabase migration fails**
→ Ensure `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` are set in `backend/.env` and that the Supabase project is in the `ca-central-1` region (PIPEDA requirement).

---

## See Also

- [ENVIRONMENT_VARIABLES.md](./ENVIRONMENT_VARIABLES.md) — full variable reference for all four surfaces
- [backend/.env.example](../backend/.env.example) — annotated backend template
- [rider-app/.env.example](../rider-app/.env.example) — rider app template
- [driver-app/.env.example](../driver-app/.env.example) — driver app template
