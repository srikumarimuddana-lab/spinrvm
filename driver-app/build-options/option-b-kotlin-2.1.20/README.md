# Option B — Kotlin 2.1.20 strategy (driver-app)

**Status:** Cold storage. NOT active.

Same as `rider-app/build-options/option-b-kotlin-2.1.20/README.md` but **without Stripe**.

## How to switch FROM Option C TO Option B

1. **Remove the Kotlin patch** (revert libs.versions.toml to upstream):
   ```bash
   rm patches/@react-native+gradle-plugin+0.85.2.patch
   rm -rf node_modules && yarn install
   ```
2. **Edit `app.config.ts`:**
   - Change `kotlinVersion: '2.2.21'` → `'2.1.20'`
3. **Update `withKspVersion.js`:**
   ```js
   const KSP_VERSION = '2.1.20-2.0.1';
   ```
4. **Local verify** with `npx expo prebuild --clean --platform android`.

See `rider-app/build-options/option-b-kotlin-2.1.20/README.md` for full context.
